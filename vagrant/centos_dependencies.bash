#!/usr/bin/env bash

# Exit immediately if any untested command fails.
set -e

cd /root

echo ========== Installing PostgreSQL ==========
yum install -q -y postgresql-server postgresql-contrib
postgresql-setup initdb
systemctl enable postgresql
systemctl start postgresql

echo ========== Installing Singularity ==========
yum groupinstall -q -y 'Development Tools'
yum install -q -y libarchive-devel squashfs-tools python-devel sqlite-devel
git clone https://github.com/singularityware/singularity.git
cd singularity
git checkout -q tags/2.5.2
./autogen.sh
./configure
make dist
cd ..

# Install from rpm instead of directly from make, so it can be uninstalled.
rpmbuild --quiet -ta singularity/singularity-2.5.2.tar.gz
yum install -q -y rpmbuild/RPMS/x86_64/singularity-2.5.2-1.el7.centos.x86_64.rpm \
    rpmbuild/RPMS/x86_64/singularity-runtime-2.5.2-1.el7.centos.x86_64.rpm
rm -rf singularity rpmbuild

echo ========== Installing Docker ==========
yum install -q -y yum-utils \
  device-mapper-persistent-data \
  lvm2
yum-config-manager \
    --add-repo \
    https://download.docker.com/linux/centos/docker-ce.repo
yum install -q -y docker-ce
systemctl enable docker
systemctl start docker

echo ========== Installing MySQL/MariaDB for Slurm ==========
yum install -q -y mariadb-server mariadb-devel
systemctl enable mariadb
systemctl start mariadb

echo ========== Installing Slurm ==========
mkdir --parents /etc/slurm
cp  /usr/local/share/Kive/vagrant/slurm.conf \
    /usr/local/share/Kive/vagrant/cgroup.conf \
    /usr/local/share/Kive/vagrant/slurmdbd.conf \
    /etc/slurm/
mysql --execute "create user slurm@localhost;"
mysql --execute "grant all on slurm_acct_db.* TO slurm@localhost;"
mysql --execute "create database slurm_acct_db;"
yum install -q -y epel-release
yum install -q -y munge munge-libs munge-devel rng-tools
rngd -r /dev/urandom
create-munge-key -r
systemctl enable munge
systemctl start munge
yum install -q -y openssl openssl-devel pam-devel numactl numactl-devel \
    hwloc hwloc-devel lua lua-devel readline-devel rrdtool-devel \
    ncurses-devel man2html libibmad libibumad rpm-build perl-devel
wget -q https://download.schedmd.com/slurm/slurm-17.11.9-2.tar.bz2
rpmbuild --quiet -ta slurm-17.11.9-2.tar.bz2
yum install -q -y \
    rpmbuild/RPMS/x86_64/slurm-17.11.9-2.el7.centos.x86_64.rpm \
    rpmbuild/RPMS/x86_64/slurm-example-configs-17.11.9-2.el7.centos.x86_64.rpm \
    rpmbuild/RPMS/x86_64/slurm-slurmctld-17.11.9-2.el7.centos.x86_64.rpm \
    rpmbuild/RPMS/x86_64/slurm-slurmd-17.11.9-2.el7.centos.x86_64.rpm \
    rpmbuild/RPMS/x86_64/slurm-slurmdbd-17.11.9-2.el7.centos.x86_64.rpm
rm -rf slurm-17.11.9-2.tar.bz2 rpmbuild
useradd --system slurm
mkdir /var/log/slurm /var/run/slurm /var/lib/slurm
chown slurm:slurm /var/log/slurm/ /var/run/slurm/ /var/lib/slurm
chown -R slurm:slurm /etc/slurm/
chmod o-r /etc/slurm/slurmdbd.conf
sed -i -e 's|/var/run/slurmdbd.pid|/var/run/slurm/slurmdbd.pid|' \
    /usr/lib/systemd/system/slurmdbd.service
sed -i -e 's|/var/run/slurmctld.pid|/var/run/slurm/slurmctld.pid|' \
    /usr/lib/systemd/system/slurmctld.service
sed -i -e 's|/var/run/slurmd.pid|/var/run/slurm/slurmd.pid|' \
    /usr/lib/systemd/system/slurmd.service
systemctl daemon-reload
systemctl enable slurmdbd
systemctl start slurmdbd
systemctl enable slurmctld
systemctl start slurmctld
systemctl enable slurmd
systemctl start slurmd

sacctmgr -i add cluster localhost
systemctl restart slurmdbd
systemctl restart slurmctld

echo ========== Installing Apache ==========
# httpd is already installed.
yum install -q -y mod_wsgi

useradd --system kive
mkdir /home/kive /etc/kive /var/log/kive
chown kive:kive /home/kive /etc/kive /var/log/kive
chmod go-rx /home/kive /etc/kive /var/log/kive

cp /usr/local/share/Kive/vagrant_ubuntu/001-kive.conf /etc/httpd/conf.d/
sed -e 's/^export //' /usr/local/share/Kive/vagrant_ubuntu/envvars.conf >> /etc/sysconfig/httpd
echo "KIVE_LOG=/var/log/kive/kive_apache.log" >> /etc/sysconfig/httpd
# KIVE_SECRET_KEY gets added to /etc/sysconfig/httpd in the Kive section below.

chmod g-r,o-r /etc/sysconfig/httpd
sed -e 's/Listen 80$/Listen 8080/' \
    -e 's/User apache$/User kive/' \
    -e 's/Group apache$/Group kive/' -i /etc/httpd/conf/httpd.conf
systemctl enable httpd
systemctl start httpd