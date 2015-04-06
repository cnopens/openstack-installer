# Copyright 2014, 2015 Canonical, Ltd.
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

from collections import defaultdict, Counter
from enum import Enum
import logging
import yaml
from multiprocessing import cpu_count

from cloudinstall.maas import (satisfies, MaasMachineStatus)
from cloudinstall.utils import load_charms
from cloudinstall.state import CharmState

log = logging.getLogger('cloudinstall.placement')


class AssignmentType(Enum):
    BareMetal = 1
    KVM = 2
    LXC = 3

DEFAULT_SHARED_ASSIGNMENT_TYPE = AssignmentType.LXC


class PlaceholderMachine:

    """A dummy MaasMachine that doesn't map to an actual machine in MAAS.

    To specify a future virtual machine for placement that will be
    created later, pass in vm specs as juju constraints in 'constraints'.

    The keys juju uses differ somewhat from the MAAS API status keys,
    and are mapped so that they will appear correct to placement code
    expecting MAAS machines.
    """

    def __init__(self, instance_id, name, constraints=None):
        self.instance_id = instance_id
        self.system_id = instance_id
        self.machine_id = -1
        self.display_name = name
        def_cons = {'arch': '?',
                    'cpu_count': 0,
                    'cpu_cores': 0,
                    'memory': 0,
                    'mem': 0,
                    'storage': 0}
        if constraints is None:
            self.constraints = def_cons
        else:
            self.constraints = constraints

    @property
    def arch(self):
        return self.constraints['arch']

    @property
    def cpu_cores(self):
        return self.constraints['cpu_cores']

    def filter_label(self):
        return self.display_name

    @property
    def machine(self):
        return self.constraints

    @property
    def mem(self):
        return self.constraints['mem']

    @property
    def status(self):
        return MaasMachineStatus.UNKNOWN

    @property
    def storage(self):
        return self.constraints['storage']

    @property
    def hostname(self):
        return self.display_name

    def __repr__(self):
        return "<Placeholder Machine: {}>".format(self.display_name)


class PlacementError(Exception):

    "Generic exception class for placement related errors"


class PlacementController:

    """Keeps state of current machines and their assigned services.
    """

    def __init__(self, maas_state=None, config=None):
        self.config = config
        self.maas_state = maas_state
        self._machines = []
        self.sub_placeholder = PlaceholderMachine('_subordinates',
                                                  'Subordinate Charms')
        # assignments is {id: {atype: [charm class]}}
        self.assignments = defaultdict(lambda: defaultdict(list))
        self.autosave_filename = None
        self.reset_unplaced()

    def __repr__(self):
        return "<PlacementController {}>".format(id(self))

    def set_autosave_filename(self, filename):
        self.autosave_filename = filename

    def do_autosave(self):
        if not self.autosave_filename:
            return
        with open(self.autosave_filename, 'w') as af:
            self.save(af)

    def save(self, f):
        """f is a file-like object to save state to, to be re-read by
        load(). No guarantees made about the contents of the file.
        """
        flat_assignments = {}
        for iid, ad in self.assignments.items():
            constraints = {}
            if self.maas_state is None:
                machine = next((m for m in self.machines() if
                                m.instance_id == iid), None)
                if machine:
                    constraints = machine.constraints

            flat_ad = {}
            for atype, al in ad.items():
                flat_al = [cc.charm_name for cc in al]
                flat_ad[atype.name] = flat_al

            flat_assignments[iid] = dict(constraints=constraints,
                                         assignments=flat_ad)
        yaml.dump(flat_assignments, f)

    def load(self, f):
        """Load assignments from file object written to by save().
        replaces current assignments.
        """
        def find_charm_class(name):
            for cc in self.charm_classes():
                if cc.charm_name == name:
                    return cc
            log.warning("Could not find charm class "
                        "matching saved charm name {}".format(name))
            return None

        file_assignments = yaml.load(f)
        new_assignments = defaultdict(lambda: defaultdict(list))
        for iid, d in file_assignments.items():
            if self.maas_state is None and \
               iid != self.sub_placeholder.instance_id:
                constraints = d['constraints']
                pm = PlaceholderMachine(iid, iid,
                                        constraints)
                self._machines.append(pm)
            for atypestr, al in d['assignments'].items():
                new_al = [find_charm_class(ccname)
                          for ccname in al]
                new_al = [x for x in new_al if x is not None]
                at = AssignmentType.__members__[atypestr]
                new_assignments[iid][at] = new_al

        self.assignments.clear()
        self.assignments.update(new_assignments)
        self.reset_unplaced()

    def update_and_save(self):
        self.reset_unplaced()
        self.do_autosave()

    def machines(self, include_placeholders=True):
        """Returns all machines known to the controller.

        if 'include_placeholder' is False, any placeholder machines
        are excluded.
        """
        if self.maas_state:
            ms = self.maas_state.machines()
        else:
            ms = self._machines

        if include_placeholders:
            return ms + [self.sub_placeholder]
        else:
            return ms

    def machines_used(self, include_placeholders=False):
        """Returns a list of machines that have charms placed on them.

        Excludes placeholder machines by default, so this can be used
        to e.g. get the number of real machines to wait for.
        """
        ms = []
        for m in self.machines(include_placeholders=include_placeholders):
            if m.instance_id in self.assignments:
                n = sum(len(cl) for _, cl in
                        self.assignments[m.instance_id].items())
                if n > 0:
                    ms.append(m)
        return ms

    def charm_classes(self):
        cl = [m.__charm_class__ for m in
              load_charms(self.config.getopt('charm_plugin_dir'))
              if not m.__charm_class__.disabled]

        return cl

    def placed_charm_classes(self):
        "Returns a deduplicated list of all charms that have a placement"
        return [cc for cc in self.charm_classes()
                if cc not in self.unplaced_services]

    def assign(self, machine, charm_class, atype):
        if not charm_class.allow_multi_units:
            for m, d in self.assignments.items():
                for at, l in d.items():
                    if charm_class in l:
                        l.remove(charm_class)

        self.assignments[machine.instance_id][atype].append(charm_class)
        self.update_and_save()

    def machines_for_charm(self, charm_class):
        """ returns assignments for a given charm
        returns {assignment_type : [machines]}
        """
        all_machines = self.machines()
        machines_by_atype = defaultdict(list)
        for m_id, d in self.assignments.items():
            for atype, assignment_list in d.items():
                for a in assignment_list:
                    if a == charm_class:
                        m = next((m for m in all_machines
                                  if m.instance_id == m_id), None)
                        if m:
                            machines_by_atype[atype].append(m)
        return machines_by_atype

    def clear_all_assignments(self):
        self.assignments = defaultdict(lambda: defaultdict(list))
        self.update_and_save()

    def clear_assignments(self, m):
        """clears all assignments for machine m.
        If m has no assignments, does nothing.
        """
        if m.instance_id not in self.assignments:
            return

        del self.assignments[m.instance_id]
        self.update_and_save()

    def remove_one_assignment(self, m, cc):
        ad = self.assignments[m.instance_id]
        for atype, assignment_list in ad.items():
            if cc in assignment_list:
                assignment_list.remove(cc)
                break
        self.update_and_save()

    def assignments_for_machine(self, m):
        """Returns all assignments for given machine

        {assignment_type: [charm_class]}
        """
        return self.assignments[m.instance_id]

    def is_assigned(self, charm_class, machine):
        assignment_dict = self.assignments[machine.instance_id]
        for atype, charm_classes in assignment_dict.items():
            if charm_class in charm_classes:
                return True
        return False

    def set_all_assignments(self, assignments):
        self.assignments = assignments
        self.update_and_save()

    def reset_unplaced(self):
        self.unplaced_services = set()
        for cc in self.charm_classes():
            md = self.machines_for_charm(cc)
            is_placed = False
            for atype, ml in md.items():
                if len(ml) > 0:
                    is_placed = True
            if not is_placed:
                self.unplaced_services.add(cc)

    def get_charm_state(self, charm):
        """Returns tuple of charm state:
        (state, cons, deps)

        state is a CharmState:

        - REQUIRED means that the charm still must be placed before
        deploying is OK.

        IF a charm dependency forced this, then the other charm will
        be in 'deps'.  'deps' is NOT just a list of all charms that
        depend on the given charm.

        - CONFLICTED means that it can't be placed until a conflicting
        charm is unplaced.  In this case, the conflicting charm is in
        'cons'.

        - OPTIONAL means that it is ok either way. deps and cons are unused

        """
        state = CharmState.OPTIONAL
        conflicting = set()
        depending = set()

        def conflicts_with(other_charm):
            return (charm.charm_name in other_charm.conflicts or
                    other_charm.charm_name in charm.conflicts)

        def depends(a_charm, b_charm):
            return b_charm.charm_name in a_charm.depends

        required_charms = [c for c in self.charm_classes()
                           if c.is_core]

        placed_or_required = self.placed_charm_classes() + required_charms

        for other_charm in placed_or_required:
            if conflicts_with(other_charm):
                state = CharmState.CONFLICTED
                conflicting.add(other_charm)
            if depends(other_charm, charm):
                if state != CharmState.CONFLICTED:
                    state = CharmState.REQUIRED
                depending.add(other_charm)

        if charm in required_charms:
            state = CharmState.REQUIRED

        n_required = charm.required_num_units()
        # sanity check:
        if n_required > 1 and not charm.allow_multi_units:
            log.error("Inconsistent charm definition for {}:"
                      " - requires {} units but does not allow "
                      "multi units.".format(charm.charm_name, n_required))

        n_units = self.machine_count_for_charm(charm)

        if state == CharmState.OPTIONAL and \
           n_units > 0 and n_units < n_required:
            state = CharmState.REQUIRED
        elif state == CharmState.REQUIRED and n_units >= n_required:
            state = CharmState.OPTIONAL

        return (state, list(conflicting), list(depending))

    def can_deploy(self):
        unplaced_requireds = [cc for cc in self.unplaced_services
                              if self.get_charm_state(cc)[0] ==
                              CharmState.REQUIRED]

        return len(unplaced_requireds) == 0

    def machine_count_for_charm(self, cc):
        """Returns the total number of placements of any type for a given
        charm."""
        return sum([len(al) for al in self.machines_for_charm(cc).values()])

    def autoplace_unplaced_services(self):
        """Attempt to find machines for all required unplaced services using
        only empty machines.

        Returns a pair (success, message) where success is True if all
        services are placed. message is an info message for the user.

        """

        empty_machines = [m for m in self.machines()
                          if len(self.assignments[m.instance_id]) == 0]

        unplaced_defaults = self.gen_defaults(list(self.unplaced_services),
                                              empty_machines)

        for mid, charm_classes in unplaced_defaults.items():
            self.assignments[mid] = charm_classes

        self.update_and_save()

        unplaced_reqs = [c for c in self.unplaced_services if
                         self.get_charm_state(c)[0] == CharmState.REQUIRED]

        if len(unplaced_reqs) > 0:
            msg = ("Not enough empty machines could be found for the following"
                   "required services. Please add machines or finish "
                   "placement manually.")
            m = ", ".join([c.charm_name for c in unplaced_reqs])
            return (False, msg + "\n" + m)
        return (True, "")

    def gen_defaults(self, charm_classes=None, maas_machines=None):
        """Generates an assignments dictionary for the given charm classes and
        machines, based on constraints.

        Does not alter controller state.

        Use set_all_assignments(gen_defaults()) to clear and reset the
        controller's state to these defaults.

        Should not be used for single installs, see gen_single.
        """
        if self.maas_state is None:
            raise PlacementError("Can't call gen_defaults with no maas_state")

        if charm_classes is None:
            charm_classes = self.charm_classes()

        assignments = defaultdict(lambda: defaultdict(list))

        if maas_machines is None:
            maas_machines = self.maas_state.machines(MaasMachineStatus.READY)

        def satisfying_machine(constraints):
            for machine in maas_machines:
                if satisfies(machine, constraints)[0]:
                    maas_machines.remove(machine)
                    return machine

            return None

        isolated_charms, controller_charms = [], []
        subordinate_charms = []

        for charm_class in charm_classes:
            state, _, _ = self.get_charm_state(charm_class)
            if state != CharmState.REQUIRED:
                continue
            if charm_class.isolate:
                assert(not charm_class.subordinate)
                isolated_charms.append(charm_class)
            elif charm_class.subordinate:
                assert(not charm_class.isolate)
                subordinate_charms.append(charm_class)
            else:
                controller_charms.append(charm_class)

        for charm_class in isolated_charms:
            for n in range(charm_class.required_num_units()):
                m = satisfying_machine(charm_class.constraints)
                if m:
                    l = assignments[m.instance_id][AssignmentType.BareMetal]
                    l.append(charm_class)

        controller_machine = satisfying_machine({})
        if controller_machine:
            for charm_class in controller_charms:
                ad = assignments[controller_machine.instance_id]
                l = ad[DEFAULT_SHARED_ASSIGNMENT_TYPE]
                l.append(charm_class)

        for charm_class in subordinate_charms:
            ad = assignments[self.sub_placeholder.instance_id]
            # BareMetal is arbitrary, it is ignored in deploy:
            l = ad[AssignmentType.BareMetal]
            l.append(charm_class)

        import pprint
        log.debug(pprint.pformat(assignments))
        return assignments

    def gen_single(self):
        """Generates an assignment for the single installer."""
        assignments = defaultdict(lambda: defaultdict(list))

        max_cpus = cpu_count()
        if max_cpus >= 2:
            max_cpus = min(8, max_cpus // 2)

        controller = PlaceholderMachine('controller', 'controller',
                                        {'mem': 6144,
                                         'root-disk': 20480,
                                         'cpu-cores': max_cpus})
        self._machines.append(controller)

        charm_name_counter = Counter()

        def placeholder_for_charm(charm_class):
            mnum = charm_name_counter[charm_class.charm_name]
            charm_name_counter[charm_class.charm_name] += 1

            instance_id = '{}-machine-{}'.format(charm_class.charm_name,
                                                 mnum)
            m_name = 'machine {} for {}'.format(mnum,
                                                charm_class.display_name)

            return PlaceholderMachine(instance_id, m_name,
                                      charm_class.constraints)

        for charm_class in self.charm_classes():
            state, _, _ = self.get_charm_state(charm_class)
            if state != CharmState.REQUIRED:
                continue
            if charm_class.isolate:
                assert(not charm_class.subordinate)
                for n in range(charm_class.required_num_units()):
                    pm = placeholder_for_charm(charm_class)
                    self._machines.append(pm)
                    ad = assignments[pm.instance_id]
                    # in single, "BareMetal" is in a KVM on the host
                    ad[AssignmentType.BareMetal].append(charm_class)
            elif charm_class.subordinate:
                assert(not charm_class.isolate)
                ad = assignments[self.sub_placeholder.instance_id]
                l = ad[AssignmentType.BareMetal]
                l.append(charm_class)
            else:
                ad = assignments[controller.instance_id]
                ad[AssignmentType.LXC].append(charm_class)

        import pprint
        log.debug("gen_single() = '{}'".format(pprint.pformat(assignments)))
        return assignments
