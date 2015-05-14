#
# Copyright 2014 Canonical, Ltd.
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

from cloudinstall.config import (INSTALL_TYPE_SINGLE,
                                 INSTALL_TYPE_MULTI,
                                 INSTALL_TYPE_LANDSCAPE)
from cloudinstall.state import InstallState
from cloudinstall.single_install import SingleInstall
from cloudinstall.landscape_install import LandscapeInstall
from cloudinstall.multi_install import (MultiInstallNewMaas,
                                        MultiInstallExistingMaas)
import cloudinstall.utils as utils


log = logging.getLogger('cloudinstall.install')


class InstallController:

    """ Install controller """

    # These are overriden in tests
    SingleInstall = SingleInstall
    MultiInstallExistingMaas = MultiInstallExistingMaas
    MultiInstallNewMaas = MultiInstallNewMaas
    LandscapeInstall = LandscapeInstall

    def __init__(self, ui, config, loop):
        self.ui = ui
        self.config = config
        self.loop = loop
        self.install_type = None
        self.config.setopt('current_state', InstallState.RUNNING.value)
        if not self.config.getopt('headless'):
            if self.config.getopt('openstack_release') == 'icehouse':
                self.ui.set_openstack_rel("Icehouse (2014.1.3)")
            elif self.config.getopt('openstack_release') == 'juno':
                self.ui.set_openstack_rel("Juno (2014.2.2)")
            else:
                self.ui.set_openstack_rel("Kilo (2015.1.0)")

    def _set_install_type(self, install_type):
        self.install_type = install_type
        self.ui.show_password_input(
            'Create a New OpenStack Password', self._save_password)

    def _save_password(self, creds):
        """ Checks passwords match and proceeds
        """
        password = creds['password'].value
        if 'confirm_password' in creds:
            confirm_password = creds['confirm_password'].value
        if password and password == confirm_password:
            self.ui.flash_reset()
            self.loop.redraw_screen()
            self.config.setopt('openstack_password', password)
            self.ui.hide_show_password_input()
            self.do_install()
        else:
            self.ui.flash('Passwords did not match')
            self.loop.redraw_screen()
            return self.ui.show_password_input(
                'Create a New OpenStack Password', self._save_password)

    def _save_maas_creds(self, creds):
        self.ui.hide_widget_on_top()
        maas_server = creds['maas_server'].value
        maas_apikey = creds['maas_apikey'].value

        if maas_server and maas_apikey:
            if maas_server.startswith("http"):
                self.ui.flash('Please enter the MAAS server\'s '
                              'IP address only, not a full URL')
                return self.ui.select_maas_type(self._save_maas_creds)
            self.config.setopt('maascreds', dict(api_host=maas_server,
                                                 api_key=maas_apikey))
            log.info("Performing a Multi Install with existing MAAS")
            return self.MultiInstallExistingMaas(
                self.loop, self.ui, self.config).run()
        else:
            log.info("Performing a Multi Install with new MAAS")
            return self.MultiInstallNewMaas(
                self.loop, self.ui, self.config).run()

    def update(self, *args, **kwargs):
        "periodically check for display changes"
        if self.config.getopt('current_state') == InstallState.RUNNING:
            pass
        elif self.config.getopt('current_state') == InstallState.NODE_WAIT:
            self.ui.render_machine_wait_view(self.config)
            self.loop.redraw_screen()

        self.loop.set_alarm_in(1, self.update)

    def do_install(self):
        """ Perform install
        """
        if not self.config.getopt('headless'):
            self.ui.hide_selector_info()

        # Set installed placeholder
        utils.spew(os.path.join(
            self.config.cfg_path, 'installed'), 'auto-generated')
        if self.install_type == INSTALL_TYPE_SINGLE[0]:
            self.ui.status_info_message("Performing a Single Install")
            self.SingleInstall(
                self.loop, self.ui, self.config).run()
        elif self.install_type == INSTALL_TYPE_MULTI[0]:
            # TODO: Clean this up a bit more I dont like relying on
            # opts.headless but in a few places
            if self.config.getopt('headless'):
                if self.config.getopt('maascreds'):
                    self.ui.status_info_message(
                        "Performing a Multi install with existing MAAS")
                    self.MultiInstallExistingMaas(
                        self.loop, self.ui, self.config).run()
                else:
                    self.MultiInstallNewMaas(
                        self.loop, self.ui, self.config).run()
            else:
                self.ui.select_maas_type(self._save_maas_creds)
        elif self.install_type == INSTALL_TYPE_LANDSCAPE[0]:
            log.info("Performing a Landscape OpenStack Autopilot install")
            self.LandscapeInstall(
                self.loop, self.ui, self.config).run()
        else:
            os.remove(os.path.join(self.config.cfg_path, 'installed'))
            raise ValueError("Unknown install type: {}".format(
                self.install_type))

    def start(self):
        """ Start installer eventloop
        """
        if self.config.getopt('headless'):
            self.install_type = self.config.getopt('install_type')
            if not self.install_type:
                log.error('Fatal error: '
                          'Unable to read install type from configuration.')
                self.loop.exit(1)
            try:
                self.do_install()
            except:
                log.exception("Fatal error")
                self.loop.exit(1)

        else:
            self.ui.status_info_message(
                "Choose the OpenStack installation path")

            self.ui.select_install_type(
                self.config.install_types(), self._set_install_type)

        self.update()
        self.loop.run()
