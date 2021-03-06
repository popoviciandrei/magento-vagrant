import hashlib
import json
import os
import os.path
import re
import string

from xml.etree import ElementTree
from fabric.api import local, env, run, get, cd, lcd, task

config = json.load(open('/vagrant/config.json'))

project_root = os.path.join('/vagrant/projects' ,config['magento_host_url'])
magento_root = os.path.join(project_root, 'htdocs')


env.hosts = [config['ssh_host']]
env.port = config['ssh_port']
env.user = config['ssh_username']

# Set up SSH for password or certificate based authentication
env.password = config['ssh_password'] or None
if config['ssh_certificate']:
    env.key_filename = os.path.join('/vagrant', config['ssh_certificate'])
else:
    env.key_filename = None

def random_filename(extension):
    extension = '.' + extension if extension else ''
    return hashlib.md5(os.urandom(64)).hexdigest() + extension

@task
def clean_up():
    """Remove the project directory to get ready."""
    local('rm -rf %s' % project_root)
    local('mkdir -p %s' % project_root)

@task
def git_clone():
    """Clone a clean Magento installation into htdocs."""
    with lcd(project_root):
        """Clone the project"""
        local('git clone %s .' % config['project_mirror'])
        if config['project_version'] != "*":
            local('git checkout "%s"' % config['project_version'])    

        """Clone magento repository"""
        if len(config['magento_mirror']):
            local('mkdir %s' % magento_root)    
            with lcd(magento_root):
                local('git clone %s .' % config['magento_mirror'])
                if config['magento_version'] != "*":
                    local('git checkout "%s"' % config['magento_version'])



@task
def get_local_xml():
    """Copy local.xml.local or generate from  template and put the right values into it"""

    if os.path.isfile('/vagrant/local.xml.local') == True:
        local('cp /vagrant/local.xml.local %s'  % os.path.join(magento_root, 'app/etc/local.xml'))
    else:
        with lcd(magento_root):
            local('n98-magerun.phar local-config:generate 127.0.0.1 root root %s db admin' % config['magento_host_url'])
        

@task 
def create_vhost_conf():
    """Create magento host file and restart nginx"""
    host_file = config['magento_host_url'] + ".conf"
    local('cp magento.conf.template /etc/nginx/conf.d/%s' % host_file)
    with lcd('/etc/nginx/conf.d'):
        local('sed -i \'s/$MAGENTO_HOST_URL/%s/g\' %s' % (config['magento_host_url'], host_file))
        local('sed -i \'s/$MAGENTO_ROOT/%s/g\' %s' % (re.escape(magento_root), host_file))
    
    """ Add the project url to the /etc/hosts"""
    exists = local('grep -iran "%s" /etc/hosts | wc -l' % config['magento_host_url'], capture=True)
    if exists == "0":
        local('cp /etc/hosts /tmp/hosts')
        fl = open('/tmp/hosts','a+');
        fl.write('127.0.0.1 %s \n' % config['magento_host_url'])
        fl.close()
        local('sudo cp /tmp/hosts /etc/hosts && rm /tmp/hosts')
    
    """ Restart nginx """
    local('sudo service nginx restart')

@task
def get_media_dump():
    """SSH to remote server and get media folder.

    This uses the magical N98-Magerun, and it needs to be a fairly
    recent version, so make sure that it's installed somewhere globally
    accessible.

    In config.json, there is a field to supply the path to it, so there
    really is no excuse.

    The dump is then downloaded and unzipped into the Magento instance.
    """
    media_filename = random_filename('zip')
    media_location = os.path.join(config['tmp_dir'], media_filename)

    php = run("which php")
    with cd(config['magento_root']):
        run('%s %s media:dump --strip %s' %
                (php, config['magerun'], media_location))

    get(remote_path=media_location, local_path='/tmp')

    with lcd('/tmp'):
        local('unzip %s' % media_filename)
        local('cp -r media %s' % magento_root)

@task
def create_database():
    """Create the database specified locally."""
    with lcd(magento_root):
        local('n98-magerun.phar db:create')

@task
def get_database_dump():
    """Get a database dump from the server.

    This dumps the data and imports it, assuming that the database
    has been created.
    """
    php = run("which php")
    gzip = run('which gzip')

    db_filename = random_filename('sql')
    db_location = os.path.join(config['tmp_dir'], db_filename)
    with cd(config['magento_root']):
        run('%s %s db:dump -f %s --strip="@development"' %
                (php, config['magerun'], db_location))

    """Gzip the sql dump to download it faster"""    
    run(gzip + " " + db_location)    
    db_location = db_location + ".gz"
       

    get(remote_path=db_location, local_path='/tmp')
    run("rm " + db_location);

    gunzip = local('which gunzip', capture=True)
    local(gunzip + " /tmp/" + db_filename + ".gz")

    with lcd(magento_root):
        local('n98-magerun.phar db:import %s' % '/tmp/' + db_filename)
        local("rm /tmp/" + db_filename)

@task
def install_dependencies():
    """Run composer.phar install."""
    with lcd(project_root):
        local('composer.phar install')

@task
def update_dependencies():
    """Run composer.phar update."""
    with lcd(project_root):
        local('composer.phar update')

@task
def configure():
    """Grab bag of things that need doing.

    Note that arbitrary config settings can be added in config.json,
    under "other_config"
    """
    with lcd(magento_root):
        local('n98-magerun.phar config:set web/secure/base_url %s' %
                config['magento_base_url'])
        local('n98-magerun.phar config:set web/unsecure/base_url %s' %
                config['magento_base_url'])
        for rule in config['other_config']:
            local('n98-magerun.phar config:set %s' % rule)

@task
def compass():
    """Compile all compass projects."""
    with lcd(magento_root):
        configs_rb = string.split(local ('find -L skin/frontend -type f | grep "config.rb" | grep -v "rwd"', capture=True), '\n')
        for config_rb in configs_rb:
            compass_dir = local('dirname %s' % config_rb, capture=True);
            local('compass clean %s' % compass_dir)
            local('compass compile %s -e "development"' % compass_dir)


@task
def clean_cache():
    """Flush Magento caches."""
    with lcd(magento_root):
        local('n98-magerun.phar cache:flush')

@task
def init_local():
    """Init project localy(!)"""
    clean_up()
    git_clone()
    get_local_xml()
    create_vhost_conf()
    create_database()
    install_dependencies()
    compass()
    
@task
def init_remote():
    """Init project localy & get remote media and db dump files(!)"""
    init_local()
    get_remote()
    clean_cache()

@task
def get_remote():
    """ Get media and db dump from remote host for existing project(!)"""
    get_database_dump()
    configure()
    get_media_dump()