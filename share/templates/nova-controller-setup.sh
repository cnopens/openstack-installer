#!/bin/bash -e

. /tmp/openstack-admin-rc

if [[ "$2" == "Single" ]]; then
    if neutron net-list|grep -q ubuntu-net; then exit 0; fi
    # configure external network for Single install path
    {% if openstack_release in ['icehouse', 'juno'] %}
    neutron net-create --router:external=True ext-net
    {% else %}
    neutron net-create --router:external ext-net
    {% endif %}
    neutron subnet-create --name ext-subnet --gateway 10.0.{{N}}.1 --allocation-pool start=10.0.{{N}}.200,end=10.0.{{N}}.254 --disable-dhcp ext-net 10.0.{{N}}.0/24
fi

# adjust tiny image
nova flavor-delete m1.tiny
nova flavor-create m1.tiny 1 512 8 1

# create ubuntu user
keystone tenant-create --name ubuntu --description "Created by Juju"
keystone user-create --name ubuntu --tenant ubuntu --pass "$1" --email juju@localhost
keystone user-role-add --user ubuntu --role Member --tenant ubuntu

. /tmp/openstack-ubuntu-rc

# create vm network on Single only
if [[ "$2" == "Single" ]]; then
    neutron net-create ubuntu-net
    neutron subnet-create --name ubuntu-subnet --gateway 10.0.5.1 --dns-nameserver 10.0.{{N}}.1 ubuntu-net 10.0.5.0/24
    neutron router-create ubuntu-router
    neutron router-interface-add ubuntu-router ubuntu-subnet
    neutron router-gateway-set ubuntu-router ext-net

    # create pool of floating ips
    i=0
    while [ $i -ne 5 ]; do
      neutron floatingip-create ext-net
      i=$((i + 1))
    done
fi

# configure security groups
nova secgroup-add-rule default icmp -1 -1 0.0.0.0/0
nova secgroup-add-rule default tcp 22 22 0.0.0.0/0

# import key pair
nova keypair-add --pub-key /tmp/id_rsa.pub ubuntu-keypair
