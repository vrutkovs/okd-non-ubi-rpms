#!/bin/python
import sys
import subprocess
import json
from functools import cache
from pprint import pprint
from tqdm import tqdm

IGNORE_LIST=[
  # These are FCOS
  'fedora-coreos', 'machine-os-content'
  # Rebuilt using CentOS on CI
  'ironic', 'ironic-agent',
]
UBI_CONTAINER_NAME='ubi'

if len(sys.argv) != 2:
  raise ValueError('Please provide OKD release name.')

release = sys.argv[1]

def run_in_ubi_container(cmds):
  podman_cmd = ['podman', 'exec', '-ti', UBI_CONTAINER_NAME] + cmds
  return subprocess.run(podman_cmd, capture_output=False, encoding='utf-8', stdout=subprocess.DEVNULL)

def is_ubi_container_running():
  podman_cmd = ['podman', 'inspect', UBI_CONTAINER_NAME]
  return subprocess.run(podman_cmd, capture_output=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).returncode == 0

@cache
def ubi_container_has_rpm(rpm):
  cmd = ["dnf", "provides", str(rpm)]
  return run_in_ubi_container(cmd).returncode == 0

def start_ubi_container():
  ubi_cmd = ['podman', 'run', '--name=ubi', '--rm', '-d', '--security-opt=label=disable', '--entrypoint=bash', '-i', 'registry.access.redhat.com/ubi9/ubi', '-c', 'sleep infinity']
  subprocess.check_output(ubi_cmd)
  run_in_ubi_container(['dnf', 'check-update'])


def fetch_rpms_list_in_image(image):
  rpms = []
  try:
    rpms_cmd = ['podman', 'run', '--rm', '--security-opt=label=disable', '--entrypoint=rpm', '-i', image, '--queryformat', '%{NAME}\n', '-qa']
    rpms_stdout = subprocess.check_output(rpms_cmd)
    rpms = set([x.decode('utf-8') for x in rpms_stdout.split()])
  finally:
    # subprocess.run(['podman', 'rmi', '-f', image])
    pass
  return rpms



print(f'Starting UBI container and refreshing repos')
if not is_ubi_container_running():
  start_ubi_container()

print(f'Inspecting {release}')

content_pull_specs_cmd = ['oc', 'adm', 'release', 'info', f'quay.io/openshift/okd:{release}','--pullspecs', '-o', 'json']
print(f'Running {content_pull_specs_cmd}')
content_pull_specs_str = subprocess.check_output(content_pull_specs_cmd)
content_pull_specs = json.loads(content_pull_specs_str)

content_images = dict()
for image in content_pull_specs['references']['spec']['tags']:
  content_images[image['name']] = image['from']['name']

print(f'Found {len(content_images)} images')

all_rpms_in_image = dict()
progress = tqdm(content_images.items())
for image, pull_spec in progress:
  progress.set_description(f'Fetching RPM list from {image}')
  if image in IGNORE_LIST:
    print(f'Skipping {image}')
    continue
  all_rpms_in_image[image] = set(fetch_rpms_list_in_image(pull_spec))

print('Finding rpms which are not installable from UBI container')
not_found_in_ubi = dict()
progress = tqdm(all_rpms_in_image.items())
for image, rpms in progress:
  progress.set_description(f'Processing {image}')
  not_found_in_ubi[image] = set()

  subprogress = tqdm(rpms, leave=False)
  for rpm in subprogress:
    subprogress.set_description(f'Looking up {rpm} in UBI')
    if not ubi_container_has_rpm(rpm):
      not_found_in_ubi[image].add(rpm)

pprint(not_found_in_ubi)
