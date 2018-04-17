#
# OtterTune - fabfile.py
#
# Copyright (c) 2017-18, Carnegie Mellon University Database Group
#
'''
Created on Mar 23, 2018

@author: bohan
'''
import sys
import json
import logging
import time
import os.path
import re
from multiprocessing import Process
from fabric.api import (env, local, task, lcd)
from fabric.state import output as fabric_output

LOG = logging.getLogger()
LOG.setLevel(logging.DEBUG)
Format = logging.Formatter("%(asctime)s [%(levelname)s]  %(message)s")  # pylint: disable=invalid-name

# print the log
ConsoleHandler = logging.StreamHandler(sys.stdout)  # pylint: disable=invalid-name
ConsoleHandler.setFormatter(Format)
LOG.addHandler(ConsoleHandler)

# Fabric environment settings
env.hosts = ['localhost']
fabric_output.update({
    'running': True,
    'stdout': True,
})

with open('driver_config.json', 'r') as f:
    CONF = json.load(f)


@task
def check_disk_usage():
    partition = CONF['database_disk']
    disk_use = 0
    cmd = "df -h {}".format(partition)
    out = local(cmd, capture=True).splitlines()[1]
    m = re.search('\d+(?=%)', out)  # pylint: disable=anomalous-backslash-in-string
    if m:
        disk_use = int(m.group(0))
    LOG.info("Current Disk Usage: %s%s", disk_use, '%')
    return disk_use


@task
def restart_database():
    if CONF['database_type'] == 'postgres':
        cmd = 'sudo service postgresql restart'
    else:
        raise Exception("Database Type {} Not Implemented !".format(CONF['database_type']))
    local(cmd)


@task
def drop_database():
    if CONF['database_type'] == 'postgres':
        cmd = "PGPASSWORD={} dropdb -e --if-exists {} -U {}".\
              format(CONF['password'], CONF['database_name'], CONF['username'])
    else:
        raise Exception("Database Type {} Not Implemented !".format(CONF['database_type']))
    local(cmd)


@task
def create_database():
    if CONF['database_type'] == 'postgres':
        cmd = "PGPASSWORD={} createdb -e {} -U {}".\
              format(CONF['password'], CONF['database_name'], CONF['username'])
    else:
        raise Exception("Database Type {} Not Implemented !".format(CONF['database_type']))
    local(cmd)


@task
def change_conf():
    next_conf = 'next_config'
    if CONF['database_type'] == 'postgres':
        cmd = 'sudo python PostgresConf.py {} {}'.format(next_conf, CONF['database_conf'])
    else:
        raise Exception("Database Type {} Not Implemented !".format(CONF['database_type']))
    local(cmd)


@task
def load_oltpbench():
    cmd = "./oltpbenchmark -b {} -c {} --create=true --load=true".\
          format(CONF['oltpbench_workload'], CONF['oltpbench_config'])
    with lcd(CONF['oltpbench_home']):  # pylint: disable=not-context-manager
        local(cmd)


@task
def run_oltpbench():
    cmd = "./oltpbenchmark -b {} -c {} --execute=true -s 5 -o outputfile".\
          format(CONF['oltpbench_workload'], CONF['oltpbench_config'])
    with lcd(CONF['oltpbench_home']):  # pylint: disable=not-context-manager
        local(cmd)


@task
def run_oltpbench_bg():
    cmd = "./oltpbenchmark -b {} -c {} --execute=true -s 5 -o outputfile > "\
          "/home/shulij/ottertune/client/driver/oltp.log 2>&1 &".\
          format(CONF['oltpbench_workload'], CONF['oltpbench_config'])
    with lcd(CONF['oltpbench_home']):  # pylint: disable=not-context-manager
        local(cmd)


@task
def run_controller():
    cmd = 'sudo gradle run -PappArgs="-c ' \
          'config/sample_postgres_config.json -d output/postgres/" --no-daemon'
    with lcd("../controller"):  # pylint: disable=not-context-manager
        local(cmd)


@task
def stop_controller():
    cmd = 'sudo ./stop_experiment.sh'
    with lcd("../controller"):  # pylint: disable=not-context-manager
        local(cmd)


@task
def upload_result():
    cmd = 'python ../../server/website/script/upload/upload.py \
           ../controller/output/postgres/ {} {}'.format(CONF['upload_code'], CONF['upload_url'])
    local(cmd)


@task
def get_result():
    cmd = 'python ../../script/query_and_get.py https://ottertune.cs.cmu.edu {} 5'.\
          format(CONF['upload_code'])
    local(cmd)


def _ready_to_start_controller():
    file_path = '/home/shulij/ottertune/client/driver/oltp.log'
    if not os.path.exists(file_path):
        return False
    else:
        lines = open(file_path).readlines()
        return len(lines) >= 15 and len(lines[14].split(' ')) > 2


def _ready_to_shut_down_controller():
    pid_file_path = '/home/shulij/ottertune/client/controller/pid.txt'
    file_path = '/home/shulij/ottertune/client/driver/oltp.log'
    if not os.path.exists(pid_file_path) or not os.path.exists(file_path):
        return False
    else:
        lines = open(file_path).readlines()
        return len(lines) >= 21 and len(lines[20].split(' ')) > 2


@task
def loop():
    max_disk_usage = 80

    # restart database
    restart_database()

    # check disk usage
    if check_disk_usage() > max_disk_usage:
        LOG.info('Exceeds max disk usage %s, reload database'.max_disk_usage)
        drop_database()
        create_database()
        load_oltpbench()
        LOG.info('Reload database Done !')

    # run oltpbench as a background job
    run_oltpbench_bg()

    # run controller from another process
    p = Process(target=run_controller, args=())
    while not _ready_to_start_controller():
        pass
    p.start()
    while not _ready_to_shut_down_controller():
        pass
    # stop the experiment
    stop_controller()

    # to prevent the race condition, wait for the controller to wrap up
    time.sleep(20)

    # upload result
    upload_result()

    # get result
    get_result()

    # change config
    change_conf()


@task
def run_loops(max_iter=2):
    for i in range(int(max_iter)):
        LOG.info('The %s-th Loop Starts / Total Loops %s', i + 1, max_iter)
        loop()
        LOG.info('The %s-th Loop Ends / Total Loops %s', i + 1, max_iter)
