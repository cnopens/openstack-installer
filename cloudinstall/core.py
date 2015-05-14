# Copyright 2015 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import time
import random
import sys
import requests

from os import path, getenv

from operator import attrgetter

from cloudinstall import utils
from cloudinstall.state import ControllerState
from cloudinstall.juju import JujuState
from cloudinstall.maas import (connect_to_maas, FakeMaasState,
                               MaasMachineStatus)
from cloudinstall.charms import CharmQueue
from cloudinstall.log import PrettyLog
from cloudinstall.placement.controller import (PlacementController,
                                               AssignmentType)

from macumba import JujuClient
from macumba import Jobs as JujuJobs


log = logging.getLogger('cloudinstall.core')
sys.excepthook = utils.global_exchandler


class FakeJujuState:

    @property
    def services(self):
        return []

    def machines(self):
        return []

    def invalidate_status_cache(self):
        "does nothing"


class Controller:

    """ Controller for Juju deployments and Maas machine init """

    def __init__(self, ui, config, loop):
        self.ui = ui
        self.ui.controller = self
        self.config = config
        self.loop = loop
        self.juju_state = None
        self.juju = None
        self.maas = None
        self.maas_state = None
        self.nodes = []
        self.juju_m_idmap = None  # for single, {instance_id: machine id}
        self.deployed_charm_classes = []
        self.placement_controller = None
        self.config.setopt('current_state', ControllerState.INSTALL_WAIT.value)

    def update(self, *args, **kwargs):
        """Render UI according to current state and reset timer

        PegasusGUI only.
        """
        interval = 1

        current_state = self.config.getopt('current_state')
        if current_state == ControllerState.PLACEMENT:
            self.ui.render_placement_view(self.loop,
                                          self.config,
                                          self.commit_placement)

        elif current_state == ControllerState.INSTALL_WAIT:
            self.ui.render_node_install_wait(message="Waiting...")
            interval = self.config.node_install_wait_interval
        elif current_state == ControllerState.ADD_SERVICES:
            self.ui.render_add_services_dialog(self.deploy_new_services,
                                               self.cancel_add_services)
        elif current_state == ControllerState.SERVICES:
            self.update_node_states()
        else:
            raise Exception("Internal error, unexpected display "
                            "state '{}'".format(current_state))

        self.loop.redraw_screen()
        self.loop.set_alarm_in(interval, self.update)

    def update_node_states(self):
        """ Updating node states

        PegasusGUI only
        """
        if not self.juju_state:
            return
        deployed_services = sorted(self.juju_state.services,
                                   key=attrgetter('service_name'))
        deployed_service_names = [s.service_name for s in deployed_services]

        charm_classes = sorted(
            [m.__charm_class__ for m in
             utils.load_charms(self.config.getopt('charm_plugin_dir'))
             if m.__charm_class__.charm_name in
             deployed_service_names],
            key=attrgetter('charm_name'))

        self.nodes = list(zip(charm_classes, deployed_services))

        for n in deployed_services:
            for u in n.units:
                if u.is_horizon and u.agent_state == "started":
                    self.ui.set_dashboard_url(
                        u.public_address, 'ubuntu',
                        self.config.getopt('openstack_password'))
                if u.is_jujugui and u.agent_state == "started":
                    self.ui.set_jujugui_url(u.public_address)
        if len(self.nodes) == 0:
            return
        else:
            self.ui.render_services_view(self.nodes, self.juju_state,
                                         self.maas_state, self.config)

    def authenticate_juju(self):
        if not len(self.config.juju_env['state-servers']) > 0:
            state_server = 'localhost:17070'
        else:
            state_server = self.config.juju_env['state-servers'][0]
        self.juju = JujuClient(
            url=path.join('wss://', state_server),
            password=self.config.juju_api_password)
        self.juju.login()
        self.juju_state = JujuState(self.juju)
        log.debug('Authenticated against juju api.')

    def initialize(self):
        """Authenticates against juju/maas and sets up placement controller."""
        if getenv("FAKE_API_DATA"):
            self.juju_state = FakeJujuState()
            self.maas_state = FakeMaasState()
        else:
            self.authenticate_juju()
            if self.config.is_multi():
                creds = self.config.getopt('maascreds')
                self.maas, self.maas_state = connect_to_maas(creds)

        self.placement_controller = PlacementController(
            self.maas_state, self.config)

        if path.exists(self.config.placements_filename):
            with open(self.config.placements_filename, 'r') as pf:
                self.placement_controller.load(pf)
            self.ui.status_info_message("Loaded placements from file.")
            log.info("Loaded placements from "
                     "'{}'".format(self.config.placements_filename))

            # If we have no machines (so we are a fresh install) but
            # are reading a placements.yaml from a previous install,
            # so it has no assignments, only deployments, tell the
            # controller to use the deployments in the file as
            # assignments:
            if len(self.placement_controller.machines_pending()) == 0 and \
               len(self.juju_state.machines()) == 0:
                self.placement_controller.set_assignments_from_deployments()
                log.info("Using deployments saved from previous install"
                         " as new assignments.")
        else:
            if self.config.is_multi():
                def_assignments = self.placement_controller.gen_defaults()
            else:
                def_assignments = self.placement_controller.gen_single()

            self.placement_controller.set_all_assignments(def_assignments)

        pfn = self.config.placements_filename
        self.placement_controller.set_autosave_filename(pfn)
        self.placement_controller.do_autosave()

        if self.config.is_single():
            if self.config.getopt('headless'):
                self.begin_deployment()
            else:
                self.begin_deployment_async()
            return

        if self.config.getopt('edit_placement') or \
           not self.placement_controller.can_deploy():
            self.config.setopt(
                'current_state', ControllerState.PLACEMENT.value)
        else:
            if self.config.getopt('headless'):
                self.begin_deployment()
            else:
                self.begin_deployment_async()

    @utils.async
    def wait_for_maas_async(self):
        """ explicit async method
        """
        self.wait_for_maas()

    def wait_for_maas(self):
        """ install and configure maas """
        random_status = ["Packages are being installed to a MAAS container.",
                         "There's a few packages, it'll take just a minute",
                         "Checkout http://maas.ubuntu.com/ while you wait."]
        is_connected = False
        count = 0
        while not is_connected:
            self.ui.render_node_install_wait(message="Waiting...")
            self.ui.status_info_message(
                random_status[random.randrange(len(random_status))])
            count = count + 1
            self.ui.status_info_message(
                "Waiting for MAAS (tries {0})".format(count))
            uri = path.join('http://', utils.container_ip('maas'), 'MAAS')
            log.debug("Checking MAAS availability ({0})".format(uri))
            try:
                res = requests.get(uri)
                is_connected = res.ok
            except:
                self.ui.status_info_message("Waiting for MAAS to be installed")
            time.sleep(10)

        # Render nodeview, even though nothing is there yet.
        self.initialize()

    def commit_placement(self):
        self.config.setopt('current_state', ControllerState.SERVICES.value)
        self.ui.render_services_view(self.nodes, self.juju_state,
                                     self.maas_state, self.config)
        self.loop.redraw_screen()
        if self.config.getopt('headless'):
            self.begin_deployment()
        else:
            self.begin_deployment_async()

    @utils.async
    def begin_deployment_async(self):
        """ async deployment
        """
        self.begin_deployment()

    def begin_deployment(self):
        if self.config.is_multi():

            # now all machines are added
            self.maas.tag_fpi(self.maas.nodes)
            self.maas.nodes_accept_all()
            self.maas.tag_name(self.maas.nodes)

            while not self.all_maas_machines_ready():
                time.sleep(3)

            self.add_machines_to_juju_multi()

        elif self.config.is_single():
            self.add_machines_to_juju_single()

        # Quiet out some of the logging
        _previous_summary = None
        while not self.all_juju_machines_started():
            sd = self.juju_state.machines_summary()
            summary = ", ".join(["{} {}".format(v, k) for k, v
                                 in sd.items()])
            if summary != _previous_summary:
                self.ui.status_info_message("Waiting for machines to "
                                            "start: {}".format(summary))
                _previous_summary = summary

            time.sleep(1)

        if len(self.juju_state.machines()) == 0:
            raise Exception("Expected some juju machines started.")

        self.config.setopt('current_state', ControllerState.SERVICES.value)
        if self.config.is_single():
            controller_machine = self.juju_m_idmap['controller']
            self.configure_lxc_network(controller_machine)

            for juju_machine_id in self.juju_m_idmap.values():
                self.run_apt_go_fast(juju_machine_id)

        if self.config.is_single():
            self.set_unique_hostnames()

        self.deploy_using_placement()
        self.wait_for_deployed_services_ready()
        self.enqueue_deployed_charms()

    def set_unique_hostnames(self):
        """checks for and ensures unique hostnames, so e.g. ceph can assume
        that.

        FIXME: Remove once http://pad.lv/1326091 is fixed
        """
        count = 0
        for machine in self.juju_state.machines():
            count += 1
            hostname = machine.machine.get('InstanceId',
                                           "ubuntu-{}".format(count))

            log.debug("Setting hostname of {} to {}".format(machine,
                                                            hostname))
            juju_home = self.config.juju_home(use_expansion=True)
            utils.remote_run(
                machine.machine_id,
                cmds="echo {} | sudo tee /etc/hostname".format(hostname),
                juju_home=juju_home)
            utils.remote_run(
                machine.machine_id,
                cmds="sudo hostname {}".format(hostname),
                juju_home=juju_home)

    def all_maas_machines_ready(self):
        self.maas_state.invalidate_nodes_cache()

        needed = set([m.instance_id for m in
                      self.placement_controller.machines_pending()])
        ready = set([m.instance_id for m in
                     self.maas_state.machines(MaasMachineStatus.READY)])
        allocated = set([m.instance_id for m in
                         self.maas_state.machines(MaasMachineStatus.ALLOCATED)
                         ])

        summary = ", ".join(["{} {}".format(v, k) for k, v in
                             self.maas_state.machines_summary().items()])
        self.ui.status_info_message("Waiting for {} maas machines to be ready."
                                    " Machines Summary: {}".format(len(needed),
                                                                   summary))
        if not needed.issubset(ready.union(allocated)):
            return False
        return True

    def add_machines_to_juju_multi(self):
        """Adds each of the machines used for the placement to juju, if it
        isn't already there."""

        self.juju_state.invalidate_status_cache()
        juju_ids = [jm.instance_id for jm in self.juju_state.machines()]

        machine_params = []
        for maas_machine in self.placement_controller.machines_pending():
            if maas_machine.instance_id in juju_ids:
                # ignore machines that are already added to juju
                continue
            cd = dict(tags=[maas_machine.system_id])
            mp = dict(Series="", ContainerType="", ParentId="",
                      Constraints=cd, Jobs=[JujuJobs.HostUnits])
            machine_params.append(mp)

        if len(machine_params) > 0:
            import pprint
            log.debug("calling add_machines with params:"
                      " {}".format(pprint.pformat(machine_params)))
            rv = self.juju.add_machines(machine_params)
            log.debug("add_machines returned '{}'".format(rv))

    def all_juju_machines_started(self):
        self.juju_state.invalidate_status_cache()
        n_needed = len(self.placement_controller.machines_pending())
        n_allocated = len([jm for jm in self.juju_state.machines()
                           if jm.agent_state == 'started'])
        return n_allocated >= n_needed

    def add_machines_to_juju_single(self):
        self.juju_state.invalidate_status_cache()
        self.juju_m_idmap = {}
        for jm in self.juju_state.machines():
            response = self.juju.get_annotations(jm.machine_id,
                                                 'machine')
            ann = response['Annotations']
            if 'instance_id' in ann:
                self.juju_m_idmap[ann['instance_id']] = jm.machine_id

        log.debug("existing juju machines: {}".format(self.juju_m_idmap))

        def get_created_machine_id(iid, response):
            d = response['Machines'][0]
            if d['Error']:
                raise Exception("Error adding machine '{}':"
                                "{}".format(iid, response))
            else:
                return d['Machine']

        for machine in self.placement_controller.machines_pending():
            if machine.instance_id in self.juju_m_idmap:
                machine.machine_id = self.juju_m_idmap[machine.instance_id]
                log.debug("machine instance_id {} already exists as #{}, "
                          "skipping".format(machine.instance_id,
                                            machine.machine_id))
                continue
            log.debug("adding machine with "
                      "constraints={}, series={}".format(
                          machine.constraints, machine.series))

            rv = self.juju.add_machine(series=machine.series,
                                       constraints=machine.constraints)
            m_id = get_created_machine_id(machine.instance_id, rv)
            machine.machine_id = m_id
            rv = self.juju.set_annotations(m_id, 'machine',
                                           {'instance_id':
                                            machine.instance_id})
            self.juju_m_idmap[machine.instance_id] = m_id

    def run_apt_go_fast(self, machine_id):
        utils.remote_cp(machine_id,
                        src=path.join(self.config.share_path,
                                      "tools/apt-go-fast"),
                        dst="/tmp/apt-go-fast",
                        juju_home=self.config.juju_home(use_expansion=True))
        utils.remote_run(machine_id,
                         cmds="sudo sh /tmp/apt-go-fast",
                         juju_home=self.config.juju_home(use_expansion=True))

    def configure_lxc_network(self, machine_id):
        # upload our lxc-host-only template and setup bridge
        log.info('Copying network specifications to machine')
        srcpath = path.join(self.config.tmpl_path, 'lxc-host-only')
        destpath = "/tmp/lxc-host-only"
        utils.remote_cp(machine_id, src=srcpath, dst=destpath,
                        juju_home=self.config.juju_home(use_expansion=True))
        log.debug('Updating network configuration for machine')
        utils.remote_run(machine_id,
                         cmds="sudo chmod +x /tmp/lxc-host-only",
                         juju_home=self.config.juju_home(use_expansion=True))
        utils.remote_run(machine_id,
                         cmds="sudo /tmp/lxc-host-only",
                         juju_home=self.config.juju_home(use_expansion=True))

    def deploy_using_placement(self):
        """Deploy charms using machine placement from placement controller,
        waiting for any deferred charms.  Then enqueue all charms for
        further processing and return.
        """

        self.ui.status_info_message("Verifying service deployments")
        assigned_ccs = self.placement_controller.assigned_charm_classes()
        charm_classes = sorted(assigned_ccs,
                               key=attrgetter('deploy_priority'))

        def undeployed_charm_classes():
            return [c for c in charm_classes
                    if c not in self.deployed_charm_classes]

        def update_pending_display():
            pending_names = [c.display_name for c in
                             undeployed_charm_classes()]
            self.ui.set_pending_deploys(pending_names)

        while len(undeployed_charm_classes()) > 0:
            update_pending_display()

            for charm_class in undeployed_charm_classes():
                self.ui.status_info_message(
                    "Checking if {c} is deployed".format(
                        c=charm_class.display_name))

                service_names = [s.service_name for s in
                                 self.juju_state.services]

                if charm_class.charm_name in service_names:
                    self.ui.status_info_message(
                        "{c} is already deployed, skipping".format(
                            c=charm_class.display_name))
                    self.deployed_charm_classes.append(charm_class)
                    continue

                err = self.try_deploy(charm_class)
                name = charm_class.display_name
                if err:
                    log.debug(
                        "{} is waiting for another service, will"
                        " re-try in a few seconds".format(name))
                    break
                else:
                    log.debug("Issued deploy for {}".format(name))
                    self.deployed_charm_classes.append(charm_class)

                self.juju_state.invalidate_status_cache()
                update_pending_display()

            num_remaining = len(undeployed_charm_classes())
            if num_remaining > 0:
                log.debug("{} charms pending deploy.".format(num_remaining))
                log.debug("deployed_charm_classes={}".format(
                    PrettyLog(self.deployed_charm_classes)))

                time.sleep(5)
            update_pending_display()

    def try_deploy(self, charm_class):
        "returns True if deploy is deferred and should be tried again."

        charm = charm_class(juju=self.juju,
                            juju_state=self.juju_state,
                            ui=self.ui,
                            config=self.config)

        asts = self.placement_controller.get_assignments(charm_class)
        errs = []
        first_deploy = True
        for atype, ml in asts.items():
            for machine in ml:
                mspec = self.get_machine_spec(machine, atype)
                if mspec is None:
                    errs.append(machine)
                    continue

                if first_deploy:
                    msg = "Deploying {c}".format(c=charm_class.display_name)
                    if mspec != '':
                        msg += " to machine {mspec}".format(mspec=mspec)
                    self.ui.status_info_message(msg)
                    deploy_err = charm.deploy(mspec)
                    if deploy_err:
                        errs.append(machine)
                    else:
                        first_deploy = False
                else:
                    # service already deployed, need to add-unit
                    msg = ("Adding one unit of "
                           "{c}".format(c=charm_class.display_name))
                    if mspec != '':
                        msg += " to machine {mspec}".format(mspec=mspec)
                    self.ui.status_info_message(msg)
                    deploy_err = charm.add_unit(machine_spec=mspec)
                    if deploy_err:
                        errs.append(machine)
                if not deploy_err:
                    self.placement_controller.mark_deployed(machine,
                                                            charm_class,
                                                            atype)

        had_err = len(errs) > 0
        if had_err and not self.config.getopt('headless'):
            log.warning("deferred deploying to these machines: {}".format(
                errs))
        return had_err

    def get_machine_spec(self, maas_machine, atype):
        """Given a machine and assignment type, return a juju machine spec.

        Returns None on errors, and '' for the subordinate char placeholder.
        """
        if self.placement_controller.is_placeholder(maas_machine.instance_id):
            # placeholder machines do not use a machine spec
            return ""

        jm = next((m for m in self.juju_state.machines()
                   if (m.instance_id == maas_machine.instance_id or
                       m.machine_id == maas_machine.machine_id)), None)
        if jm is None:
            log.error("could not find juju machine matching {}"
                      " (instance id {})".format(maas_machine,
                                                 maas_machine.instance_id))

            return None

        if atype == AssignmentType.BareMetal \
           or atype == AssignmentType.DEFAULT:
            return jm.machine_id
        elif atype == AssignmentType.LXC:
            return "lxc:{}".format(jm.machine_id)
        elif atype == AssignmentType.KVM:
            return "kvm:{}".format(jm.machine_id)
        else:
            log.error("unexpected atype: {}".format(atype))
            return None

    def wait_for_deployed_services_ready(self):
        """ Blocks until all deployed services attached units
        are in a 'started' state
        """
        if not self.juju_state:
            return

        self.ui.status_info_message(
            "Waiting for deployed services to be in a ready state.")

        not_ready_len = 0
        while not self.juju_state.all_agents_started():
            not_ready = [(a, b) for a, b in self.juju_state.get_agent_states()
                         if b != 'started']
            if len(not_ready) == not_ready_len:
                time.sleep(3)
                continue

            not_ready_len = len(not_ready)
            log.info("Checking availability of {} ".format(
                ", ".join(["{}:{}".format(a, b) for a, b in not_ready])))
            time.sleep(3)

        self.ui.status_info_message(
            "Processing relations and finalizing services")

    def enqueue_deployed_charms(self):
        """Send all deployed charms to CharmQueue for relation setting and
        post-proc.
        """
        charm_q = CharmQueue(ui=self.ui, config=self.config,
                             juju=self.juju, juju_state=self.juju_state,
                             deployed_charms=self.deployed_charm_classes)

        if self.config.getopt('headless'):
            charm_q.watch_relations()
            charm_q.watch_post_proc()
        else:
            charm_q.watch_relations_async()
            charm_q.watch_post_proc_async()
        charm_q.is_running = True

        # Exit cleanly if we've finished all deploys, relations,
        # post processing, and running in headless mode.
        if self.config.getopt('headless'):
            while not self.config.getopt('deploy_complete'):
                self.ui.status_info_message(
                    "Waiting for services to be started.")
                # FIXME: Is this needed?
                # time.sleep(10)
            self.ui.status_info_message(
                "All services deployed, relations set, and started")
            self.loop.exit(0)

        self.ui.status_info_message(
            "Services deployed, relationships may still be"
            " pending. Please wait for all services to be checked before"
            " deploying compute nodes")
        self.ui.render_services_view(self.nodes, self.juju_state,
                                     self.maas_state, self.config)
        self.loop.redraw_screen()

    @utils.async
    def deploy_new_services(self):
        """Deploys newly added services in background thread.
        Does not attempt to create new machines.
        """
        self.config.setopt('current_state', ControllerState.SERVICES.value)
        self.ui.render_services_view(self.nodes, self.juju_state,
                                     self.maas_state, self.config)
        self.loop.redraw_screen()

        self.deploy_using_placement()
        self.wait_for_deployed_services_ready()
        self.set_unique_hostnames()
        self.enqueue_deployed_charms()

    def cancel_add_services(self):
        """User cancelled add-services screen.
        Just redisplay services view.
        """
        self.config.setopt('current_state',
                           ControllerState.SERVICES.value)
        self.ui.render_services_view(self.nodes, self.juju_state,
                                     self.maas_state, self.config)
        self.loop.redraw_screen()

    def start(self):
        """ Starts UI loop
        """
        if self.config.getopt('headless'):
            self.initialize()
        else:
            self.ui.status_info_message("Welcome")
            self.initialize()
            self.loop.register_callback('refresh_display', self.update)
            self.loop.set_alarm_in(0, self.update)
            self.loop.run()
            self.loop.close()
