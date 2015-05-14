#!/bin/sh
# Imports proper Vivid image for use with lxd

wget -O vivid-server-cloudimg-amd64-root.tar.gz https://cloud-images.ubuntu.com/vivid/current/vivid-server-cloudimg-amd64-root.tar.gz
glance image-create --name='lxc' --container-format=bare --disk-format=raw < vivid-server-cloudimage-amd64-root.tar.gz
