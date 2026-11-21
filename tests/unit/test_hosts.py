"""Baked /etc/hosts entries survive CreateImage (ec2sim.vm.DockerVMManager.parse_extra_hosts)."""

from ec2sim.vm import DockerVMManager

DOCKER_HOSTS = """127.0.0.1\tlocalhost
::1\tlocalhost ip6-localhost ip6-loopback
fe00::0\tip6-localnet
ff00::0\tip6-mcastprefix
ff02::1\tip6-allnodes
ff02::2\tip6-allrouters
172.31.0.4\tip-172-31-0-4
"""


def test_only_provisioner_entries_are_kept():
    text = DOCKER_HOSTS + "\n# staging DBs\n10.7.1.128      stg-db1a dbnode\n10.7.1.129 stg-db2a  # replica\n10.7.1.128 dbnode\n"
    assert DockerVMManager.parse_extra_hosts(text, {"172.31.0.4"}) == [
        ["10.7.1.128", ["stg-db1a", "dbnode"]],
        ["10.7.1.129", ["stg-db2a"]],
    ]


def test_plain_docker_hosts_has_nothing_to_keep():
    assert DockerVMManager.parse_extra_hosts(DOCKER_HOSTS, {"172.31.0.4"}) == []
