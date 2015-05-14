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

from cloudinstall.charms import CharmBase


class CharmRabbitMQ(CharmBase):

    """ RabbitMQ directives """

    charm_name = 'rabbitmq-server'
    charm_rev = 32
    charm_branch = "lp:charms/trusty/rabbitmq-server"
    display_name = 'RabbitMQ Server'
    deploy_priority = 1
    related = [('rabbitmq-server:amqp',
                'neutron-openvswitch:amqp')]
    is_core = True
    have_nextbranch = True

__charm_class__ = CharmRabbitMQ
