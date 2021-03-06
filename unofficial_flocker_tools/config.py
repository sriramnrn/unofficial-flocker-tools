#!/usr/bin/env python

# This script will generate some certificates using flocker-ca and upload them
# to the servers specified in a cluster.yml

import sys
import yaml
import time
from twisted.internet.task import react
from twisted.internet.defer import inlineCallbacks, gatherResults

from os import environ

# when installing on k8s which has been set up with kube-aws, this is necc
EARLY_DOCKER_PREFIX = ""
if environ.get("EARLY_DOCKER") == "1":
    EARLY_DOCKER_PREFIX = "-H unix:///run/early-docker.sock "

# Usage: deploy.py cluster.yml
from utils import Configurator, log

def report_completion(result, public_ip, message=""):
    log(message, public_ip)
    return result

@inlineCallbacks
def main(reactor, *args):
    c = Configurator(configFile=sys.argv[1])
    c.run("flocker-ca initialize %s" % (c.config["cluster_name"],))
    log("Initialized cluster CA.")
    c.run("flocker-ca create-control-certificate %s" % (c.config["control_node"],))
    log("Created control cert.")
    node_mapping = {}
    for node in c.config["agent_nodes"]:
        public_ip = node["public"]
        # Created 8eab4b8d-c0a2-4ce2-80aa-0709277a9a7a.crt. Copy ...
        uuid = c.run("flocker-ca create-node-certificate").split(".")[0].split(" ")[1]
        node_mapping[public_ip] = uuid
        log("Generated", uuid, "for", public_ip)
    for user in c.config["users"]:
        c.run("flocker-ca create-api-certificate %s" % (user,))
        log("Created user key for", user)

    # Dump agent_config into a file and scp it to /etc/flocker/agent.yml on the
    # nodes.
    f = open("agent.yml", "w")
    yaml.dump(c.config["agent_config"], f)
    f.close()

    # Record the node mapping for later.
    f = open("node_mapping.yml", "w")
    yaml.dump(node_mapping, f)
    f.close()

    log("Making /etc/flocker directory on all nodes")
    deferreds = []
    for node, uuid in node_mapping.iteritems():
        deferreds.append(c.runSSHAsync(node, "mkdir -p /etc/flocker"))
    deferreds.append(c.runSSHAsync(c.config["control_node"], "mkdir -p /etc/flocker"))
    yield gatherResults(deferreds)

    log("Uploading keys to respective nodes:")
    deferreds = []

    # Copy cluster cert, and control cert and key to control node.
    d = c.scp("cluster.crt", c.config["control_node"], "/etc/flocker/cluster.crt", async=True)
    d.addCallback(report_completion, public_ip=c.config["control_node"], message=" * Uploaded cluster cert to")
    deferreds.append(d)

    for ext in ("crt", "key"):
        d = c.scp("control-%s.%s" % (c.config["control_node"], ext),
                c.config["control_node"], "/etc/flocker/control-service.%s" % (ext,), async=True)
        d.addCallback(report_completion, public_ip=c.config["control_node"], message=" * Uploaded control %s to" % (ext,))
        deferreds.append(d)
    log(" * Uploaded control cert & key to control node.")

    # Copy cluster cert, and agent cert and key to agent nodes.
    deferreds = []
    for node, uuid in node_mapping.iteritems():
        d = c.scp("cluster.crt", node, "/etc/flocker/cluster.crt", async=True)
        d.addCallback(report_completion, public_ip=node, message=" * Uploaded cluster cert to")
        deferreds.append(d)

        d = c.scp("agent.yml", node, "/etc/flocker/agent.yml", async=True)
        d.addCallback(report_completion, public_ip=node, message=" * Uploaded agent.yml to")
        deferreds.append(d)

        for ext in ("crt", "key"):
            d = c.scp("%s.%s" % (uuid, ext), node, "/etc/flocker/node.%s" % (ext,), async=True)
            d.addCallback(report_completion, public_ip=node, message=" * Uploaded node %s to" % (ext,))
            deferreds.append(d)

    yield gatherResults(deferreds)

    deferreds = []
    for node, uuid in node_mapping.iteritems():
        if c.config["os"] == "ubuntu":
            d = c.runSSHAsync(node, """echo "starting flocker-container-agent..."
service flocker-container-agent start
echo "starting flocker-dataset-agent..."
service flocker-dataset-agent start
""")
        elif c.config["os"] == "centos":
            d = c.runSSHAsync(node, """if selinuxenabled; then setenforce 0; fi
systemctl enable docker.service
systemctl start docker.service
""")
        elif c.config["os"] == "coreos":
            d = c.runSSHAsync(node, """echo
echo > /tmp/flocker-command-log
docker %(early_docker_prefix)s run --restart=always -d --net=host --privileged \\
    -v /etc/flocker:/etc/flocker \\
    -v /var/run/docker.sock:/var/run/docker.sock \\
    --name=flocker-container-agent \\
    clusterhq/flocker-container-agent
docker %(early_docker_prefix)s run --restart=always -d --net=host --privileged \\
    -e DEBUG=1 \\
    -v /tmp/flocker-command-log:/tmp/flocker-command-log \\
    -v /flocker:/flocker -v /:/host -v /etc/flocker:/etc/flocker \\
    -v /dev:/dev \\
    --name=flocker-dataset-agent \\
    clusterhq/flocker-dataset-agent
""" % dict(early_docker_prefix=EARLY_DOCKER_PREFIX))
        deferreds.append(d)

    if c.config["os"] == "ubuntu":
        d = c.runSSHAsync(c.config["control_node"], """cat <<EOF > /etc/init/flocker-control.override
start on runlevel [2345]
stop on runlevel [016]
EOF
echo 'flocker-control-api       4523/tcp                        # Flocker Control API port' >> /etc/services
echo 'flocker-control-agent     4524/tcp                        # Flocker Control Agent port' >> /etc/services
service flocker-control restart
ufw allow flocker-control-api
ufw allow flocker-control-agent
""")
    elif c.config["os"] == "centos":
        d = c.runSSHAsync(c.config["control_node"], """systemctl enable flocker-control
systemctl start flocker-control
firewall-cmd --permanent --add-service flocker-control-api
firewall-cmd --add-service flocker-control-api
firewall-cmd --permanent --add-service flocker-control-agent
firewall-cmd --add-service flocker-control-agent
""")
    elif c.config["os"] == "coreos":
        d = c.runSSHAsync(c.config["control_node"], """echo
docker %(early_docker_prefix)s run --name=flocker-control-volume -v /var/lib/flocker clusterhq/flocker-control-service true
docker %(early_docker_prefix)s run --restart=always -d --net=host -v /etc/flocker:/etc/flocker --volumes-from=flocker-control-volume --name=flocker-control-service clusterhq/flocker-control-service""" % dict(early_docker_prefix=EARLY_DOCKER_PREFIX))

    deferreds.append(d)

    yield gatherResults(deferreds)

    if c.config["os"] == "ubuntu":
        # XXX INSECURE, UNSUPPORTED, UNDOCUMENTED EXPERIMENTAL OPTION
        # Usage: `uft-flocker-config --ubuntu-aws --swarm`, I guess
        if len(sys.argv) > 2 and sys.argv[2] == "--swarm":
            # Install swarm
            deferreds = []
            clusterid = c.runSSH(c.config["control_node"], """
docker run swarm create""").strip()
            log("Created Swarm ID")
            for node in c.config["agent_nodes"]:
                d = c.runSSHAsync(node['public'], """
service docker stop
docker daemon -H unix:///var/run/docker.sock -H tcp://0.0.0.0:2375 >> /tmp/dockerlogs 2>&1 &
""")
                # Let daemon come up
                time.sleep(3)
                d = c.runSSHAsync(node['public'], """
docker run -d swarm join --addr=%s:2375 token://%s
""" % (node['private'], clusterid))
                log("Started Swarm Agent for %s" % node['public'])
                deferreds.append(d)

            d = c.runSSHAsync(c.config["control_node"], """
docker run -d -p 2357:2375 swarm manage token://%s
""" % clusterid)
            log("Starting Swarm Master")
            deferreds.append(d)
            yield gatherResults(deferreds)
            log("Swarm Master is at tcp://%s:2357" % c.config["control_node"])

def _main():
    react(main, sys.argv[1:])

if __name__ == "__main__":
    _main()
