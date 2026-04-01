#!/usr/bin/env python3
"""
Collect JaCoCo coverage data from storage cluster nodes.

Lifecycle:
  1. SSH to each node -> dump JaCoCo exec file via jacococli
  2. Copy exec files from all nodes to master node
  3. Merge all exec files into a single merged exec
  4. Generate HTML + XML report on the master node (if source available)
  5. SCP the report to local machine

Follows the same SSH patterns as jacoco_keywords.py but operates
standalone (no Robot Framework dependency).
"""

import argparse
import logging
import os
import stat
import sys
import time

try:
    import paramiko
except ImportError:
    paramiko = None

try:
    import yaml
except ImportError:
    yaml = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("collect_coverage")


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

class SSHClient(object):
    """Thin wrapper around paramiko for cluster node access."""

    def __init__(self, host, username, password, timeout=30,
                 container_name=None, sudo_password=None):
        if paramiko is None:
            raise ImportError(
                "paramiko is required. Install with: pip install paramiko"
            )
        self.host = host
        self.container_name = container_name
        self.sudo_password = sudo_password or password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=host,
            username=username,
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )

    def run(self, cmd, timeout=120):
        """Execute *cmd* on the host and return (stdout, stderr, exit_code)."""
        log.debug("[%s] %s", self.host, cmd)
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            log.warning(
                "[%s] command exited %d: %s\nstderr: %s",
                self.host, exit_code, cmd, err,
            )
        return out, err, exit_code

    def docker_run(self, cmd, timeout=120):
        """Execute *cmd* inside the Docker container (if configured)."""
        if self.container_name:
            # Wrap command for docker exec with sudo
            wrapped = (
                "echo {pwd} | sudo -S docker exec {ctr} bash -c '{cmd}'"
                .format(
                    pwd=self.sudo_password,
                    ctr=self.container_name,
                    cmd=cmd.replace("'", "'\\''"),
                )
            )
            return self.run(wrapped, timeout=timeout)
        else:
            return self.run(cmd, timeout=timeout)

    def get(self, remote_path, local_path):
        """Download a file via SFTP."""
        sftp = self.client.open_sftp()
        try:
            log.info("[%s] SFTP get %s -> %s", self.host, remote_path, local_path)
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def get_dir(self, remote_dir, local_dir):
        """Recursively download a directory via SFTP."""
        sftp = self.client.open_sftp()
        try:
            self._sftp_get_dir(sftp, remote_dir, local_dir)
        finally:
            sftp.close()

    def _sftp_get_dir(self, sftp, remote_dir, local_dir):
        os.makedirs(local_dir, exist_ok=True)
        for entry in sftp.listdir_attr(remote_dir):
            remote_path = remote_dir.rstrip("/") + "/" + entry.filename
            local_path = os.path.join(local_dir, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                self._sftp_get_dir(sftp, remote_path, local_path)
            else:
                log.debug("  get %s", remote_path)
                sftp.get(remote_path, local_path)

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# Node discovery
# ---------------------------------------------------------------------------

def discover_nodes(ssh, expected_count=None):
    """Get list of data node IPs from the master node."""
    # Try cluster-exec-based discovery first (standard storage cluster approach)
    out, _, rc = ssh.run(
        "sudo /opt/storage/bin/cluster-exec -i \"hostname -i\" 2>/dev/null "
        "| grep -oP '\\d+\\.\\d+\\.\\d+\\.\\d+' | sort -u"
    )
    if rc == 0 and out:
        nodes = [ip.strip() for ip in out.splitlines() if ip.strip()]
        if nodes:
            log.info("Discovered %d nodes via cluster-exec: %s", len(nodes), nodes)
            return nodes

    # Fallback: parse /etc/hosts or CM peer list
    out, _, rc = ssh.run(
        "cat /opt/storage/conf/bm.properties 2>/dev/null "
        "| grep 'node_' | grep -oP '\\d+\\.\\d+\\.\\d+\\.\\d+'"
    )
    if rc == 0 and out:
        nodes = [ip.strip() for ip in out.splitlines() if ip.strip()]
        if nodes:
            log.info("Discovered %d nodes from bm.properties: %s", len(nodes), nodes)
            return nodes

    log.warning("Could not auto-discover nodes; using master node only")
    return []


# ---------------------------------------------------------------------------
# JaCoCo operations
# ---------------------------------------------------------------------------

def dump_exec(ssh, service, port, exec_dump_dir, jacoco_lib_dir, reset=False):
    """Dump JaCoCo exec data from a running service on one node."""
    dest_file = "{dir}/{svc}_{ip}.exec".format(
        dir=exec_dump_dir, svc=service, ip="$(hostname -i)"
    )
    reset_flag = " --reset" if reset else ""

    # Ensure dump directory exists
    ssh.docker_run("mkdir -p {dir}".format(dir=exec_dump_dir))

    cmd = (
        'export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java)))) && '
        '"${{JAVA_HOME}}/bin/java" -jar {lib}/jacococli.jar dump '
        '--address=localhost --port={port} '
        '--destfile={dest}{reset}'.format(
            lib=jacoco_lib_dir,
            port=port,
            dest=dest_file,
            reset=reset_flag,
        )
    )
    out, err, rc = ssh.docker_run(cmd, timeout=60)
    if rc != 0:
        log.error("dump failed on %s: %s", ssh.host, err)
        return None

    # Get the actual filename (resolve hostname -i)
    out2, _, _ = ssh.docker_run(
        "ls -1 {dir}/{svc}_*.exec 2>/dev/null | tail -1".format(
            dir=exec_dump_dir, svc=service
        )
    )
    actual_file = out2.strip() if out2 else dest_file
    log.info("Dumped exec: %s on %s", actual_file, ssh.host)
    return actual_file


def merge_exec_files(ssh, exec_dump_dir, jacoco_lib_dir, output_file=None):
    """Merge all .exec files in the dump directory into one merged file."""
    if output_file is None:
        output_file = "{dir}/merged-java-coverage.exec".format(dir=exec_dump_dir)

    cmd = (
        'export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java)))) && '
        'EXEC_FILES=$(ls {dir}/*.exec 2>/dev/null | tr "\\n" " ") && '
        '"${{JAVA_HOME}}/bin/java" -jar {lib}/jacococli.jar merge '
        '$EXEC_FILES --destfile {out}'.format(
            dir=exec_dump_dir,
            lib=jacoco_lib_dir,
            out=output_file,
        )
    )
    out, err, rc = ssh.docker_run(cmd, timeout=120)
    if rc != 0:
        log.error("merge failed: %s", err)
        return None
    log.info("Merged exec: %s", output_file)
    return output_file


def generate_report(ssh, merged_exec, jacoco_lib_dir,
                    class_files_path, source_code_path,
                    html_dir="/tmp/jacoco-report", xml_file=None):
    """Generate JaCoCo HTML (and optionally XML) report on the node."""
    if not class_files_path:
        log.warning("No class_files_path configured; skipping report generation")
        return None

    ssh.docker_run("rm -rf {d} && mkdir -p {d}".format(d=html_dir))

    cmd = (
        'export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java)))) && '
        '"${{JAVA_HOME}}/bin/java" -jar {lib}/jacococli.jar report {exec_file} '
        '--classfiles {classes} '
        '{sources}'
        '--html {html}'.format(
            lib=jacoco_lib_dir,
            exec_file=merged_exec,
            classes=class_files_path,
            sources=(
                "--sourcefiles {src} ".format(src=source_code_path)
                if source_code_path else ""
            ),
            html=html_dir,
        )
    )

    if xml_file:
        cmd += " --xml {xml}".format(xml=xml_file)

    out, err, rc = ssh.docker_run(cmd, timeout=180)
    if rc != 0:
        log.error("report generation failed: %s", err)
        return None

    log.info("Report generated at %s on %s", html_dir, ssh.host)
    return html_dir


# ---------------------------------------------------------------------------
# Collection flow
# ---------------------------------------------------------------------------

def collect_coverage(config, round_num):
    """
    Full collection flow for one round.

    Returns dict with paths to local coverage data, or None on failure.
    """
    cluster_cfg = config["cluster"]
    target_cfg = config["target"]
    output_cfg = config["output"]

    ip = cluster_cfg["ip"]
    username = cluster_cfg["username"]
    password = cluster_cfg["password"]
    service = target_cfg["service"]
    port = target_cfg["jacoco_port"]
    jacoco_lib_dir = target_cfg.get("jacoco_lib_dir", "/opt/storage/lib")
    exec_dump_dir = target_cfg.get("exec_dump_dir", "/var/log/jacoco/coverage")
    class_files_path = target_cfg.get("class_files_path", "")
    source_code_path = target_cfg.get("source_code_path", "")

    coverage_dir = os.path.join(
        output_cfg.get("coverage_data_dir", "coverage-data"),
        "round_{n}".format(n=round_num),
    )
    html_local_dir = os.path.join(coverage_dir, "html")
    os.makedirs(html_local_dir, exist_ok=True)

    explicit_nodes = cluster_cfg.get("nodes", [])
    container_name = target_cfg.get("container_name", "service-main")

    # Host-mapped path for SFTP access to container's /var/log/jacoco
    host_log_base = target_cfg.get(
        "host_log_base",
        "/var/log/storage-service",
    )
    # Container exec_dump_dir -> host path mapping
    # /var/log/jacoco/coverage -> <host_log_base>/jacoco/coverage
    host_exec_dir = exec_dump_dir.replace(
        "/var/log", host_log_base, 1
    )

    log.info("=== Collecting coverage for round %d ===", round_num)
    log.info("Cluster: %s, Service: %s, Port: %s", ip, service, port)
    log.info("Container: %s", container_name)

    # Connect to master node
    master = SSHClient(
        ip, username, password,
        container_name=container_name,
        sudo_password=password,
    )

    try:
        # Discover nodes
        if explicit_nodes:
            nodes = explicit_nodes
        else:
            nodes = discover_nodes(master)
            if not nodes:
                nodes = [ip]

        log.info("Target nodes: %s", nodes)

        # Phase 1: Dump exec on each node
        for node_ip in nodes:
            if node_ip == ip:
                ssh = master
            else:
                ssh = SSHClient(
                    node_ip, username, password,
                    container_name=container_name,
                    sudo_password=password,
                )
            try:
                dump_exec(ssh, service, port, exec_dump_dir, jacoco_lib_dir)
            finally:
                if node_ip != ip:
                    ssh.close()

        # Phase 2: Copy exec files from other nodes to master via host SFTP
        if len(nodes) > 1:
            # Use host-level paths for SCP (container paths not accessible via SCP)
            for node_ip in nodes:
                if node_ip == ip:
                    continue
                node_ssh = SSHClient(
                    node_ip, username, password,
                    container_name=container_name,
                    sudo_password=password,
                )
                try:
                    # Make files readable by ssh user
                    node_ssh.run(
                        "echo {pwd} | sudo -S chmod -R a+rX {dir} 2>/dev/null"
                        .format(pwd=password, dir=host_exec_dir)
                    )
                    # Find exec files on this node (host path)
                    out, _, _ = node_ssh.run(
                        "ls {dir}/{svc}_*.exec 2>/dev/null".format(
                            dir=host_exec_dir, svc=service,
                        )
                    )
                    for exec_file in out.splitlines():
                        exec_file = exec_file.strip()
                        if not exec_file:
                            continue
                        fname = os.path.basename(exec_file)
                        # SCP from node to master's /tmp
                        node_ssh.run(
                            "scp -o StrictHostKeyChecking=no "
                            "{f} {user}@{master_ip}:/tmp/{fname}".format(
                                f=exec_file,
                                user=username,
                                master_ip=ip,
                                fname=fname,
                            ),
                            timeout=60,
                        )
                finally:
                    node_ssh.close()

            # Move files from /tmp to host exec dir on master, then
            # copy into container
            master.run(
                "echo {pwd} | sudo -S mv /tmp/{svc}_*.exec {dir}/ 2>/dev/null"
                .format(pwd=password, svc=service, dir=host_exec_dir)
            )

        # Verify exec files
        out, _, _ = master.docker_run(
            "ls -lh {dir}/*.exec 2>/dev/null".format(dir=exec_dump_dir)
        )
        log.info("Exec files on master:\n%s", out)

        # Phase 3: Merge
        merged_exec = merge_exec_files(master, exec_dump_dir, jacoco_lib_dir)
        if not merged_exec:
            log.error("Merge failed; aborting collection")
            return None

        # Phase 4: Generate report on cluster (inside container)
        remote_html_dir = "/tmp/jacoco-report-round{n}".format(n=round_num)
        remote_xml = "/tmp/jacoco-report-round{n}.xml".format(n=round_num)
        report_dir = generate_report(
            master, merged_exec, jacoco_lib_dir,
            class_files_path, source_code_path,
            html_dir=remote_html_dir,
            xml_file=remote_xml if class_files_path else None,
        )

        # Phase 5: Pull report to local
        # Strategy: always download XML (fast); selectively download HTML
        host_report_dir = "/tmp/jacoco-report-host-round{n}".format(n=round_num)
        xml_local = None

        if report_dir:
            # Copy report from container to host
            master.run(
                "echo {pwd} | sudo -S docker cp {ctr}:{src} {dst}".format(
                    pwd=password,
                    ctr=container_name,
                    src=remote_html_dir,
                    dst=host_report_dir,
                )
            )
            master.run(
                "echo {pwd} | sudo -S chmod -R a+rX {dir}".format(
                    pwd=password, dir=host_report_dir,
                )
            )

            # --- Download XML report first (single file, fast) ---
            xml_local = os.path.join(coverage_dir, "coverage-report.xml")
            if class_files_path:
                host_xml = "/tmp/jacoco-report-round{n}.xml".format(n=round_num)
                master.run(
                    "echo {pwd} | sudo -S docker cp {ctr}:{src} {dst}".format(
                        pwd=password, ctr=container_name,
                        src=remote_xml, dst=host_xml,
                    )
                )
                master.run(
                    "echo {pwd} | sudo -S chmod a+r {f}".format(
                        pwd=password, f=host_xml,
                    )
                )
                try:
                    master.get(host_xml, xml_local)
                    log.info("Downloaded XML report: %s", xml_local)
                except Exception as exc:
                    log.debug("XML download skipped: %s", exc)
                    xml_local = None
            else:
                xml_local = None

            # --- Download HTML selectively (only target packages) ---
            target_packages = config.get("analysis", {}).get(
                "target_packages", []
            )
            if target_packages:
                # Convert package prefixes to directory names
                # e.g. "com.example.storage.data.blockmanager" ->
                #      "com.example.storage.data.blockmanager*"
                log.info(
                    "Downloading HTML for %d target package(s) only",
                    len(target_packages),
                )
                # List remote directories to find matching packages
                out, _, _ = master.run(
                    "ls -d {dir}/*/ 2>/dev/null".format(
                        dir=host_report_dir,
                    )
                )
                remote_dirs = [
                    d.strip().rstrip("/")
                    for d in out.splitlines() if d.strip()
                ]

                for rdir in remote_dirs:
                    dirname = os.path.basename(rdir)
                    # Check if this directory matches any target package
                    if any(dirname.startswith(tp) for tp in target_packages):
                        local_pkg_dir = os.path.join(html_local_dir, dirname)
                        os.makedirs(local_pkg_dir, exist_ok=True)
                        log.info("  Downloading HTML: %s", dirname)
                        try:
                            master.get_dir(rdir, local_pkg_dir)
                        except Exception as exc:
                            log.warning(
                                "  Failed to download %s: %s", dirname, exc
                            )

                # Also grab the top-level index and resources
                for fname in ("index.html", "jacoco-sessions.html"):
                    remote_f = os.path.join(host_report_dir, fname)
                    local_f = os.path.join(html_local_dir, fname)
                    try:
                        master.get(remote_f, local_f)
                    except Exception:
                        pass
                # jacoco-resources directory
                res_remote = os.path.join(host_report_dir, "jacoco-resources")
                res_local = os.path.join(html_local_dir, "jacoco-resources")
                try:
                    os.makedirs(res_local, exist_ok=True)
                    master.get_dir(res_remote, res_local)
                except Exception:
                    pass
            else:
                # No target packages — download everything (slow)
                log.info("Downloading full HTML report to %s", html_local_dir)
                master.get_dir(host_report_dir, html_local_dir)

        else:
            log.warning(
                "Report not generated (missing class_files_path?); "
                "downloading raw exec file instead"
            )
            # Copy merged exec from container to host, then download
            host_merged = "/tmp/merged-java-coverage-round{n}.exec".format(
                n=round_num
            )
            master.run(
                "echo {pwd} | sudo -S docker cp {ctr}:{src} {dst}".format(
                    pwd=password,
                    ctr=container_name,
                    src=merged_exec,
                    dst=host_merged,
                )
            )
            master.run(
                "echo {pwd} | sudo -S chmod a+r {f}".format(
                    pwd=password, f=host_merged,
                )
            )
            merged_local = os.path.join(coverage_dir, "merged-java-coverage.exec")
            try:
                master.get(host_merged, merged_local)
                log.info("Downloaded merged exec: %s", merged_local)
            except Exception as exc:
                log.error("Failed to download merged exec: %s", exc)

        return {
            "round": round_num,
            "coverage_dir": coverage_dir,
            "html_dir": html_local_dir,
            "xml_file": xml_local,
        }

    finally:
        master.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_config(config_path):
    """Load YAML config file."""
    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Collect JaCoCo coverage from storage cluster"
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--round", type=int, default=1,
        help="Round number (default: 1)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    result = collect_coverage(config, args.round)

    if result:
        log.info("Collection complete: %s", result)
        return 0
    else:
        log.error("Collection failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
