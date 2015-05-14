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

import logging
import os.path as path
import yaml
import cloudinstall.utils as utils
from cloudinstall.charms import CharmBase, DisplayPriorities
from cloudinstall.placement.controller import AssignmentType

log = logging.getLogger('cloudinstall.charms.compute')


class CharmNovaCompute(CharmBase):

    """ Openstack Nova Compute directives """

    charm_name = 'nova-compute'
    charm_rev = 24
    charm_branch = "lp:~openstack-charmers/charms/trusty/nova-compute"
    display_name = 'Compute'
    display_priority = DisplayPriorities.Compute
    related = [('nova-compute:neutron-plugin',
                'neutron-openvswitch:neutron-plugin'),
               ('nova-compute:amqp',
                'rabbitmq-server:amqp'),
               ('mysql:shared-db',
                'nova-compute:shared-db'),
               ('nova-compute:image-service',
                'glance:image-service'),
               ('nova-cloud-controller:cloud-compute',
                'nova-compute:cloud-compute'),
               ('ntp:juju-info',
                'nova-compute:juju-info'),
               ('nova-compute:nova-ceilometer',
                'ceilometer-agent:nova-ceilometer')]
    isolate = True
    constraints = {'mem': 4096,
                   'root-disk': 40960}
    allow_multi_units = True
    allowed_assignment_types = [AssignmentType.BareMetal,
                                AssignmentType.KVM]
    is_core = True
    have_nextbranch = True

    @classmethod
    def series_required(cls):
        conf = path.join(utils.install_home(), '.cloud-install/config.yaml')
        conf_yaml = yaml.load(utils.slurp(conf))
        if conf_yaml['use_lxd']:
            # No need to keep this separate as we aren't nesting
            # KVM at this point
            cls.isolate = False
            return 'vivid'
        return 'trusty'


__charm_class__ = CharmNovaCompute
