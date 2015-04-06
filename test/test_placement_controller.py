#!/usr/bin/env python
#
# tests placement/controller.py
#
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
import os
import unittest
from unittest.mock import call, MagicMock, PropertyMock, patch
import yaml
from tempfile import NamedTemporaryFile, TemporaryFile

from cloudinstall.charms.keystone import CharmKeystone
from cloudinstall.charms.compute import CharmNovaCompute
from cloudinstall.charms.swift import CharmSwift
from cloudinstall.charms.swift_proxy import CharmSwiftProxy
from cloudinstall.charms.ceph import CharmCeph
from cloudinstall.charms.ceph_osd import CharmCephOSD
from cloudinstall.maas import MaasMachineStatus
from cloudinstall.config import Config
from cloudinstall.state import CharmState
import cloudinstall.utils as utils

from cloudinstall.placement.controller import (AssignmentType,
                                               PlacementController,
                                               PlacementError)


DATA_DIR = os.path.join(os.path.dirname(__file__), 'maas-output')

log = logging.getLogger('cloudinstall.test_placement_controller')


class PlacementControllerTestCase(unittest.TestCase):

    def setUp(self):
        self.mock_maas_state = MagicMock()
        with NamedTemporaryFile(mode='w+', encoding='utf-8') as tempf:
            utils.spew(tempf.name, yaml.dump(dict()))
            self.conf = Config({}, tempf.name)

        self.conf.setopt('storage_backend', 'none')
        self.pc = PlacementController(self.mock_maas_state,
                                      self.conf)
        self.mock_machine = MagicMock(name='machine1')
        pmid = PropertyMock(return_value='fake-instance-id-1')
        type(self.mock_machine).instance_id = pmid

        self.mock_machine_2 = MagicMock(name='machine2')
        pmid2 = PropertyMock(return_value='fake-instance-id-2')
        type(self.mock_machine_2).instance_id = pmid2

        self.mock_machines = [self.mock_machine, self.mock_machine_2]

        self.mock_maas_state.machines.return_value = self.mock_machines

    def test_machines_for_charm_atype(self):
        self.assertEqual(0, len(self.pc.machines_for_charm(CharmNovaCompute)))
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        md = self.pc.machines_for_charm(CharmNovaCompute)
        self.assertEqual(1, len(md))
        self.assertEqual(2, len(md[AssignmentType.LXC]))

    def _do_test_simple_assign_type(self, assignment_type):
        self.pc.assign(self.mock_machine, CharmNovaCompute, assignment_type)
        print("assignments is {}".format(self.pc.assignments))
        machines = self.pc.machines_for_charm(CharmNovaCompute)
        print('machines for charm is {}'.format(machines))
        self.assertEqual(machines,
                         {assignment_type: [self.mock_machine]})

        ma = self.pc.assignments_for_machine(self.mock_machine)

        self.assertEqual(ma[assignment_type], [CharmNovaCompute])

    def test_simple_assign_bare(self):
        self._do_test_simple_assign_type(AssignmentType.BareMetal)

    def test_simple_assign_lxc(self):
        self._do_test_simple_assign_type(AssignmentType.LXC)

    def test_simple_assign_kvm(self):
        self._do_test_simple_assign_type(AssignmentType.KVM)

    def test_assign_nonmulti(self):
        self.pc.assign(self.mock_machine, CharmKeystone, AssignmentType.LXC)
        self.assertEqual(self.pc.machines_for_charm(CharmKeystone),
                         {AssignmentType.LXC: [self.mock_machine]})

        self.pc.assign(self.mock_machine, CharmKeystone, AssignmentType.KVM)
        self.assertEqual(self.pc.machines_for_charm(CharmKeystone),
                         {AssignmentType.KVM: [self.mock_machine]})

        am = self.pc.assignments_for_machine(self.mock_machine)
        self.assertEqual(am[AssignmentType.KVM], [CharmKeystone])
        self.assertEqual(am[AssignmentType.LXC], [])

    def test_assign_multi(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.assertEqual(self.pc.machines_for_charm(CharmNovaCompute),
                         {AssignmentType.LXC: [self.mock_machine]})

        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.KVM)
        self.assertEqual(self.pc.machines_for_charm(CharmNovaCompute),
                         {AssignmentType.LXC: [self.mock_machine],
                          AssignmentType.KVM: [self.mock_machine]})

        ma = self.pc.assignments_for_machine(self.mock_machine)
        self.assertEqual(ma[AssignmentType.LXC], [CharmNovaCompute])
        self.assertEqual(ma[AssignmentType.KVM], [CharmNovaCompute])

    def test_remove_assignment_multi(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine_2, CharmNovaCompute,
                       AssignmentType.LXC)

        mfc = self.pc.machines_for_charm(CharmNovaCompute)

        mfc_lxc = set(mfc[AssignmentType.LXC])
        self.assertEqual(mfc_lxc, set(self.mock_machines))

        self.pc.clear_assignments(self.mock_machine)
        self.assertEqual(self.pc.machines_for_charm(CharmNovaCompute),
                         {AssignmentType.LXC: [self.mock_machine_2]})

    def test_gen_defaults(self):
        satisfies_importstring = 'cloudinstall.placement.controller.satisfies'
        with patch(satisfies_importstring) as mock_satisfies:
            mock_satisfies.return_value = (True, )
            defs = self.pc.gen_defaults(charm_classes=[CharmNovaCompute,
                                                       CharmKeystone],
                                        maas_machines=[self.mock_machine,
                                                       self.mock_machine_2])
            m1_as = defs[self.mock_machine.instance_id]
            m2_as = defs[self.mock_machine_2.instance_id]
            self.assertEqual(m1_as[AssignmentType.BareMetal],
                             [CharmNovaCompute])
            self.assertEqual(m1_as[AssignmentType.LXC], [])
            self.assertEqual(m1_as[AssignmentType.KVM], [])

            self.assertEqual(m2_as[AssignmentType.BareMetal], [])
            self.assertEqual(m2_as[AssignmentType.LXC], [CharmKeystone])
            self.assertEqual(m2_as[AssignmentType.KVM], [])

    def test_remove_one_assignment_sametype(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)

        self.pc.remove_one_assignment(self.mock_machine, CharmNovaCompute)
        md = self.pc.assignments[self.mock_machine.instance_id]
        lxcs = md[AssignmentType.LXC]
        self.assertEqual(lxcs, [CharmNovaCompute])

        self.pc.remove_one_assignment(self.mock_machine, CharmNovaCompute)
        md = self.pc.assignments[self.mock_machine.instance_id]
        lxcs = md[AssignmentType.LXC]
        self.assertEqual(lxcs, [])

    def test_remove_one_assignment_othertype(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.KVM)

        self.pc.remove_one_assignment(self.mock_machine, CharmNovaCompute)
        md = self.pc.assignments[self.mock_machine.instance_id]
        lxcs = md[AssignmentType.LXC]
        kvms = md[AssignmentType.KVM]
        self.assertEqual(1, len(lxcs) + len(kvms))

        self.pc.remove_one_assignment(self.mock_machine, CharmNovaCompute)
        md = self.pc.assignments[self.mock_machine.instance_id]
        lxcs = md[AssignmentType.LXC]
        kvms = md[AssignmentType.KVM]
        self.assertEqual(0, len(lxcs) + len(kvms))

    def test_clear_all(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine_2,
                       CharmNovaCompute, AssignmentType.KVM)
        self.pc.clear_all_assignments()
        # check that it's empty:
        self.assertEqual(self.pc.assignments, {})
        # and that it's still a defaultdict(lambda: defaultdict(list))
        mid = self.mock_machine.machine_id
        lxcs = self.pc.assignments[mid][AssignmentType.LXC]
        self.assertEqual(lxcs, [])

    def test_unplaced_starts_full(self):
        self.assertEqual(len(self.pc.unplaced_services),
                         len(self.pc.charm_classes()))

    def test_placed_charm_classes_starts_empty(self):
        self.assertEqual(0, len(self.pc.placed_charm_classes()))

    def test_reset_unplaced_none(self):
        """Assign all charms, ensure that unplaced is empty"""
        for cc in self.pc.charm_classes():
            self.pc.assign(self.mock_machine, cc, AssignmentType.LXC)

        self.pc.reset_unplaced()

        self.assertEqual(0, len(self.pc.unplaced_services))

    def test_reset_unplaced_two(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine_2, CharmKeystone, AssignmentType.KVM)
        self.pc.reset_unplaced()
        self.assertEqual(len(self.pc.charm_classes()) - 2,
                         len(self.pc.unplaced_services))

    def test_reset_excepting_compute(self):
        for cc in self.pc.charm_classes():
            if cc.charm_name == 'nova-compute':
                continue
            self.pc.assign(self.mock_machine, cc, AssignmentType.LXC)

        self.pc.reset_unplaced()
        self.assertEqual(len(self.pc.unplaced_services), 1)

    def test_get_charm_state(self):
        "Test a sampling of required services and special handling for compute"
        self.assertEqual(self.pc.get_charm_state(CharmKeystone)[0],
                         CharmState.REQUIRED)
        self.assertEqual(self.pc.get_charm_state(CharmNovaCompute)[0],
                         CharmState.REQUIRED)

    def test_one_compute_required(self):
        """after being assigned at least once, novacompute is no longer
        considered 'required' (aka required)"""
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.assertNotEqual(self.pc.get_charm_state(CharmNovaCompute)[0],
                            CharmState.REQUIRED)

    def test_swift_unrequired_then_required_default(self):
        "Swift and swift-proxy are both optional until you add swift"
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwift)[0])
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])
        self.pc.assign(self.mock_machine, CharmSwift, AssignmentType.LXC)
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwift)[0])
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

    def test_swift_unrequired_then_required_swift_backend(self):
        "Swift and swift-proxy are not optional with swift as the backend."
        self.pc.assign(self.mock_machine, CharmSwift, AssignmentType.LXC)
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwift)[0])
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

    def test_swift_proxy_unrequired_then_required_default(self):
        "Swift and swift-proxy are both optional until you add swift-proxy"
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwift)[0])
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

        self.pc.assign(self.mock_machine, CharmSwiftProxy, AssignmentType.LXC)
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwift)[0])
        # Only one swift-proxy is required, so now that we've added
        # it, it is still not required:
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

    def test_swift_proxy_unrequired_then_required_swift_backend(self):
        "Swift and swift-proxy are not optional with swift as the backend"
        self.pc.assign(self.mock_machine, CharmSwiftProxy, AssignmentType.LXC)
        self.assertEqual(CharmState.REQUIRED,
                         self.pc.get_charm_state(CharmSwift)[0])
        # Only one swift-proxy is required, so now that we've added
        # it, it is still not required:
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

    def test_storage_backends_in_is_required(self):
        # default is 'none'
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmCeph)[0])
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmCephOSD)[0])
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwift)[0])
        self.assertEqual(CharmState.OPTIONAL,
                         self.pc.get_charm_state(CharmSwiftProxy)[0])

    def test_ceph_num_required(self):
        "3 units of ceph should be required after having been placed"
        state, cons, deps = self.pc.get_charm_state(CharmCeph)
        self.assertEqual(state, CharmState.OPTIONAL)
        self.pc.assign(self.mock_machine, CharmCeph, AssignmentType.KVM)
        self.assertEqual(self.pc.get_charm_state(CharmCeph)[0],
                         CharmState.REQUIRED)
        self.pc.assign(self.mock_machine, CharmCeph, AssignmentType.KVM)
        self.pc.assign(self.mock_machine, CharmCeph, AssignmentType.KVM)
        self.assertEqual(self.pc.get_charm_state(CharmCeph)[0],
                         CharmState.OPTIONAL)

    def test_persistence(self):
        self.pc.assign(self.mock_machine, CharmNovaCompute, AssignmentType.LXC)
        self.pc.assign(self.mock_machine_2, CharmKeystone, AssignmentType.KVM)
        cons1 = PropertyMock(return_value={})
        type(self.mock_machine).constraints = cons1
        cons2 = PropertyMock(return_value={'cpu': 8})
        type(self.mock_machine_2).constraints = cons2

        with TemporaryFile(mode='w+', encoding='utf-8') as tempf:
            self.pc.save(tempf)
            tempf.seek(0)
            newpc = PlacementController(
                self.mock_maas_state, self.conf)
            newpc.load(tempf)
        self.assertEqual(self.pc.assignments, newpc.assignments)
        self.assertEqual(self.pc.machines_used(), newpc.machines_used())
        self.assertEqual(self.pc.placed_charm_classes(),
                         newpc.placed_charm_classes())

        m2 = next((m for m in newpc.machines_used()
                   if m.instance_id == 'fake-instance-id-2'))
        self.assertEqual(m2.constraints, {'cpu': 8})

    def test_load_machines_single(self):
        with NamedTemporaryFile(mode='w+', encoding='utf-8') as tempf:
            utils.spew(tempf.name, yaml.dump(dict()))
            conf = Config({}, tempf.name)

        fake_assignments = {
            'fake_iid': {'constraints': {},
                         'assignments': {'KVM':
                                         ['nova-compute']}},
            'fake_iid_2': {'constraints': {'cpu': 8},
                           'assignments':
                           {'BareMetal': ['nova-compute']}}}

        singlepc = PlacementController(
            None, conf)

        with TemporaryFile(mode='w+', encoding='utf-8') as tempf:
            yaml.dump(fake_assignments, tempf)
            tempf.seek(0)
            singlepc.load(tempf)

        self.assertEqual(set([m.instance_id for m in
                              singlepc.machines_used()]),
                         set(['fake_iid', 'fake_iid_2']))

        m2 = next((m for m in singlepc.machines_used()
                   if m.instance_id == 'fake_iid_2'))
        self.assertEqual(m2.constraints, {'cpu': 8})

    def test_load_error_mismatch_charm_name(self):
        """Should safely ignore (and log) a charm name in a placement file
        that can't be matched to a loaded charm class."""
        singlepc = PlacementController(None, self.conf)

        fake_assignments = {
            'fake_iid': {
                'constraints': {},
                'assignments': {'KVM':
                                ['non-existent']}},
            'fake_iid_2': {
                'constraints': {'cpu': 8},
                'assignments':
                {'BareMetal': ['nova-compute']}}}

        with TemporaryFile(mode='w+', encoding='utf-8') as tempf:
            yaml.dump(fake_assignments, tempf)
            tempf.seek(0)
            singlepc.load(tempf)

        self.assertEqual(set([m.instance_id for m in
                              singlepc.machines_used()]),
                         set(['fake_iid_2']))

        m2 = next((m for m in singlepc.machines_used()
                   if m.instance_id == 'fake_iid_2'))
        self.assertEqual(m2.constraints, {'cpu': 8})

    def test_is_assigned(self):
        self.assertFalse(self.pc.is_assigned(CharmSwiftProxy,
                                             self.mock_machine))
        self.pc.assign(self.mock_machine, CharmSwiftProxy, AssignmentType.LXC)
        self.assertTrue(self.pc.is_assigned(CharmSwiftProxy,
                                            self.mock_machine))

    def test_double_clear_ok(self):
        """clearing assignments for a machine that isn't assigned (anymore) is
        OK and should do nothing
        """
        self.pc.assign(self.mock_machine, CharmSwiftProxy, AssignmentType.LXC)
        self.pc.clear_assignments(self.mock_machine)
        self.pc.clear_assignments(self.mock_machine)
        self.pc.clear_assignments(self.mock_machine_2)

    def test_gen_defaults_raises_with_no_maas_state(self):
        pc = PlacementController(None, self.conf)
        self.assertRaises(PlacementError, pc.gen_defaults)

    def test_gen_defaults_uses_only_ready(self):
        """gen_defaults should only use ready machines"""
        mock_maas_state = MagicMock()
        mock_maas_state.machines.return_value = []
        c = Config()
        c.setopt('storage_backend', 'none')
        pc = PlacementController(config=c, maas_state=mock_maas_state)
        # reset the mock to avoid looking at calls from
        # PlacementController.__init__().
        mock_maas_state.reset_mock()

        pc.gen_defaults()
        # we simply check the first call because we know that
        # follow-on calls are from calls to machines_for_charm and do
        # not affect machines used for defaults
        self.assertEqual(mock_maas_state.machines.mock_calls[0],
                         call(MaasMachineStatus.READY))
