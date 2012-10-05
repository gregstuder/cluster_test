#!/usr/bin/python
"""A console reads remote commands config file, executes user's commands and
communicates with remote process managers.
"""

import argparse
import os
import process_manager
import re
import select
import shlex
import socket
import subprocess
import sys
import time
import traceback
import tempfile
import shutil

import console_config

class RemoteCommand(object):
    """A command entry for a remote process manager.

    Attributes:
        alias: (str) The alias of this command.
        command: (str) Command line to be excuted.
        address: (Tuple (str, str)) (host, port) IP/URL and port of remote
            process manager.
        state: (str) The state of the command.
        phase: (int) The phase of the command.
        wait: (boolean) Wait for command to finish on process manager.
    """

    # States used by ProcMgrProxy to record progress in callback methods.
    READY = 'READY'
    DONE = 'DONE'

    def __init__(self, host, port, user_name, command, alias=None, phase=1,
            wait=False):
        """Initialize remote command.

        Args:
            host, port: (str, str) IP/URL and port of remote process manager.
            command: (str) Command line to be excuted.
            alias: (str) If alias is None, use the command name itself as
                an alias.
            phase: (int) The phase of the command.
        """
        self.address = (host, port)
        self.user_name = user_name
        if alias is None:
            # Split command for shell.
            alias = shlex.split(command)[0]
        self.alias = alias
        self.command = command
        self.state = RemoteCommand.DONE
        self.phase = phase
        self.wait = wait


class ProcMgrProxy(object):
    """Manage all the communication to a given process manager.

    Attributes:
        address: (Tuple (str, str)) (host, port) IP/URL and port of remote
            process manager.
        user_name: (str) User name used for SSH.
        key_file: (str) The path of private key used for SSH authorization.
        remote_commands: (list of RemoteCommand) Remote commands assigned to
            a given process manager.

    Usage:
        1. setup. Copy files to remote cluster and lanch process managers
              on remote machines. Now process managers act as servers.
        2. connect. Establish sockets connecting to every process manager.
              Connection must be established before step 3-6.
        3. run. Use (start_run, run_done) to run binary on remote machines
              in a non-blocking manner.
        4. stop. Terminate binaries running in process manager.
        5. close. Close the socket to process manager on console side, while
              remote process managers are still running like a server, so that
              we can connect to them again in the future.
        6. shutdown. Stop binaries, force remote process manager to close
              the socket on its side and to exit. This command needs to wait for
              receiving EOF, so it is designed as non-blocking.
              See start_shutdown() and shutdown_done().

        Only run and shutdown need to receive the acknowledgement from process
        manager, so they are separated to two parts, sending commands and the
        callback function. We use select() to get acknowledgements in any order.
        Other methods just fire commands to process managers and return.
    """
    def __init__(self, address, user_name, key_file=None, remote_resource_downloads=None):
        self.address = address
        self.user_name = user_name
        self.key_file = key_file
        self.remote_commands = []
        self._socket = None
        self._reader = None
                
        print "Initializing proxy to host, available at : %s" % (self._ssh_str())

    def add_remote_command(self, remote_command):
        """Add remote_command that shares the same address into list.

        Args:
            remote_command (RemoteCommand).
        """
        # Check alias collision.
        if any(remote_command.alias == c.alias for c in self.remote_commands):
            return False
        self.remote_commands.append(remote_command)
        return True

    def connect(self):
        """Connect to remote process manager.

        Return: Whether connection is successfully established.
        """
        if not self.is_connected():
            try:
                self._socket = socket.create_connection(self.address)
                self._socket.setblocking(0)  #  Set to non-blocking.
                self._reader = process_manager.LineReader(self._socket)
                print "Connecting successfully", self.address
                return True
            except socket.error as e:
                print e
                self._socket = None
                return False
        else:
            print "Already connecting to", self.address

    def is_connected(self):
        """Whether connected with process manager."""
        return self._socket is not None

    def fileno(self):
        """File number used by select()."""
        return self._socket.fileno()

    def set_up(self):
        """Rsync's all files under the local directory 'bin/' to the remote
        cluster machine directory 'cluster_test_<port>' and launches an
        instance of the process_manager in the background.
        """
        
        print '\n', '=' * 20, "SETUP HOST %s" % (self.address,), '=' * 20
        
        # Rsync. Copy process_manager.py and all files in local bin/.
        path = 'cluster_test_%d' % self.address[1]
        
        pm_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'base_remote_resources'))
        
        src = [console_config._bin_path, pm_path]
        
        # To working dir in remote machine.
        dest = "%s@%s:%s" % (self.user_name, self.address[0], path)
        if not self._rsync(src, dest):
            # Rsync fails.
            print self.address, "encounters problems when synchronizing files."
            return
        
        print "Attaching downloadable resources..."
        
        all_dls = []
        if "" in console_config._remote_resource_downloads:
            all_dls.extend(console_config._remote_resource_downloads[""])
        
        if self.address[0] in console_config._remote_resource_downloads:
            all_dls.extend(console_config._remote_resource_downloads[self.address[0]])
        
        for url, rel_path in all_dls:
            if not self._download(url, rel_path):
                print self.address, "encounters problems when downloading files."
                return
        
        print "Attached."
        
        print "Running setup scripts..."
        
        if self.address[0] in console_config._setup_scripts:
            
            setup_scripts = console_config._setup_scripts[self.address[0]]
                
            temp_dir = tempfile.mkdtemp();
            
            for i, snippet in enumerate(setup_scripts):
                
                name, snippet, lang, isFile, shouldRun = snippet
                
                filename = os.path.join(temp_dir, name)
                
                if not isFile :
                    with open(filename, 'w') as file:
                        file.write(snippet)                        
                else:
                    shutil.copyfile(snippet, filename)
                    
            remote_snippet_dir = dest + "/base_remote_resources/scripts"
            
            if not self._rsync(temp_dir + os.sep, remote_snippet_dir):
                print self.address, "encounters problems when uploading snippets."
                return
            
            shutil.rmtree(temp_dir)
            
            # Run snippets
            for i, snippet in enumerate(setup_scripts):
                
                name, snippet, lang, isFile, shouldRun = snippet
                if not shouldRun: continue
                
                remote_filename = "base_remote_resources/scripts/%s" % name
                
                run_cmd = ". ./%s" % remote_filename
                
                if not self._ssh(run_cmd, use_tty=True):
                    print self.address, "encounters problems when executing snippets."
                    return
        
        
        # Use SSH to run process manager.
        # nohup python process_manager.py >process_manager.out 2>&1
        # </dev/null &
        ssh_command = 'cd base_remote_resources; bash restart_process_manager.sh %d' % self.address[1]
        
        # print ssh_command
        
        if self._ssh(ssh_command):
            print self.address, "process manager has been set up."
        else:
            print self.address, "encounters problems when running SSH."
            return
        
        print "Done."

    def _remoteScript(self, source_script):
        """Remotely executes a script from the local host"""

        

    def _download(self, url, rel_path):
        """Downloads a file to the remote host and installs it in a particular path."""
        
        tmp_dir = "TMP_DIR=`mktemp -d`;"
        wget_cmd = [ tmp_dir, "wget", "-nv", "-O", "$TMP_DIR/archive.tgz", url, ";" ]
        wget_cmd = ' '.join(wget_cmd)
        
        mkdir_cmd = "mkdir -p %s ;" % ("./remote_resources/" + rel_path)
        
        cleandir_cmd = "rm -Rf %s/* ;" % ("./remote_resources/" + rel_path)
        
        untar_cmd = [ "tar", "xf", "$TMP_DIR/archive.tgz", "-C", "./remote_resources/%s" % rel_path, ";" ]
        untar_cmd = ' '.join(untar_cmd)
        
        remove_cmd = "rm -Rf $TMP_DIR;"
        
        return self._ssh(' '.join([ wget_cmd, mkdir_cmd, cleandir_cmd, untar_cmd, remove_cmd ]))

    def _rsync(self, source, dest):
        """Copy file to/from remote machine.

        Args:
            source: (str OR list of str) The directory or the list of file names
                to be copied.
            dest: (str) The destination.
        """
        
        #print(source)
        
        # Test SSH connection.
        if not self._ssh('test 1 -eq 1', use_pwd=False):
            print "Waiting for SSH on %s with key %s" % (self.address[0], self.key_file)
            time.sleep(1)
            while not self._ssh('test 1 -eq 1', use_pwd=False):
                time.sleep(1)

        # Archive, compress, delete extraneous files from dest dirs.
        rsync = ['rsync', '-az', '--delete']

        # Use key file
        if self.key_file:
            ssh = 'ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "%s"'
            rsync.extend(['-e', ssh % self.key_file])

        if isinstance(source, list):
            rsync.extend(source)
            rsync.append(dest)
        else:
            rsync.extend([source, dest])
        
        print 'Sync files from %s to %s...' % (source, dest)
        
        if subprocess.call(rsync) == 0:
            return True
        else:
            return False

    def _ssh_str(self):
        """SSH shell command"""
        
        ssh = ['ssh',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'IdentitiesOnly=yes']
        
        cd_cmd = 'cd cluster_test_%d; ' % self.address[1]
        
        if self.key_file:
            ssh.extend(['-i', "\"" + self.key_file + "\"" ])
            
        ssh.extend([self.user_name + '@' + self.address[0], "-t", "\"" + cd_cmd + "$SHELL\""])
        
        return " ".join(ssh)

    def _ssh(self, command, use_pwd=True, use_tty=False, verbose=False):
        """Run command on remote machine.

        Args:
            command: (str) The program and its arguments.
                For example: 'python process_manager.py 2900'.
        """
        if use_pwd:
            cd_cmd = 'cd cluster_test_%d; ' % self.address[1]
        else:
            cd_cmd = ''
        ssh = ['ssh',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'IdentitiesOnly=yes']
        if self.key_file:
            ssh.extend(['-i', self.key_file])
        if use_tty:
            ssh.extend(['-t'])
            
        ssh.extend([self.user_name + '@' + self.address[0], cd_cmd + command])
        
        if verbose: print(" ".join(ssh))
        
        # Check whether ssh runs successfully.
        if subprocess.call(ssh) == 0:
            return True
        else:
            return False

    def start_run(self, phase):
        """Issues all commands for this proxy to the remote process manager.
        The command is not blocking. The call 'run_done' can be used to wait
        for response(s) from the process manager.

        Args:
            phase (int) The phase of commands we want to run.
        Return:
            If run is done and no callback is needed, return True, so that
            async_run_all will not call the callback on this proxy.
        """
        done = True
        for c in self.remote_commands:
            if c.phase == phase:
                print c.alias, ' : ', c.command
                c.state = RemoteCommand.READY
                self._start_run_binary(c)
                done = False
        return done

    def _start_run_binary(self, remote_command):
        """Send command to ProcessManager to run a binary on remote machine.

        Agrs:
            command_line: binary with arguments.
                For example, "mongod --dbpath /var/lib/mongodb/"
            alias: Alias of the binary, unique in that ProcessManager.
                Default is binary's name, "mongod" in above example.
        """
        
        command_list = [ process_manager.ProcessManager.RUN ]
        if remote_command.alias is not None:
            command_list.extend(['-as', remote_command.alias])
        if remote_command.wait:
            command_list.append('-w')
        command_list.append(remote_command.command)
        command = ' '.join(command_list) + '\n'
        self._socket.sendall(command)

    def run_done(self):
        """The callback method to process response of 'run' command.

        Return: True if all remote commands are running, thus callback is done.
        """
        lines = self._reader.read_lines()
        # TODO(siyuan): EOF
        for response in lines:
            print self.address, response
            alias, _ = ProcMgrProxy._parse_response(response)
            for c in self.remote_commands:
                if c.alias == alias:
                    c.state = RemoteCommand.DONE
                    break
        return all(c.state != RemoteCommand.READY for c in self.remote_commands)

    def stop(self):
        """Stop all commands."""
        for c in self.remote_commands:
            self._stop_binary(c.alias)

    def start_shutdown(self):
        """Shut down the process manager."""
        self._socket.sendall(process_manager.ProcessManager.SHUTDOWN)

    def shutdown_done(self):
        """Shutdown callback called when remote socket is closed.

        Return: True, meaning callback is done.
        """
        self._reader.read_lines()  # Should be None, EOF.
        print self.address, "remote process manager closed"
        self.close()
        return True  # Callback done.

    def close(self):
        """Close socket if necessary."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _stop_binary(self, alias):
        """Send command to ProcessManager to stop a running binary on remote
        machine.

        Agrs:
            alias (str): Alias of the binary, unique in that ProcessManager.
                Default is binary's name, "mongod" in above example.
        """
        if alias is None:
            command = process_manager.ProcessManager.STOP + '\n'
        else:
            command = '%s %s\n' % (process_manager.ProcessManager.STOP, alias)
        self._socket.sendall(command)

    def collect_log(self):
        """Collect logs from remote machine to local folder.

        Parameters:
            dest: (str) The path of local folder.
        """
        path = 'cluster_test_%d/*.log' % self.address[1]
        src = "%s@%s:%s" % (self.user_name, self.address[0], path)
        dest = console_config._log_path
        self._rsync(src, dest)

    def clean_all(self):
        """Clean all process manager, mongod, mongos and mongo."""
        for p in ['process_manager.py', 'mongo']:
            cmd = ("ps aux | grep %s | grep -v grep | awk '{ print $2 }'"
            " | xargs kill -s 9") % p
            self._ssh(cmd, use_pwd=False)

    @staticmethod
    def _parse_response(response):
        """Parse response to (alias, status)."""
        m = re.match(r"^(?P<alias>[^\s]*)\s+(?P<resp>.*)$", response)
        return m.group('alias'), m.group('resp')


class Console(object):
    """Console deploys test system, manages process managers and reports
        the progress.
    """
    def __init__(self):
        self._done = False
        self._remote_commands = []
        self._process_managers = []
        self._key_file = None

    def config(self, command_config_path):
        """Configure Console with a command config file."""
        
        # Read configs
        # Run console config in console_config module.
        m = sys.modules['console_config']
        
        # Save the current dir
        cwd = os.getcwd()
                
        # Open and execute remote command config file.
        with open(command_config_path, 'r') as command_file:
            try:
                # Reset the current path to the command file's directory
                os.chdir(os.path.dirname(command_config_path))
    
                exec command_file in m.__dict__
                        
            except Exception as e:
                print "Error in command config file:"
                print e
                print traceback.format_exc()
        
        # Reset the path
        os.chdir(cwd)
        
        alias_set = set()
        self._remote_commands = []
        for c in console_config._remote_commands:
            if c.alias not in alias_set:
                self._remote_commands.append(c)
            else:
                # Duplicated command alias.
                print 'Global duplicated alias (ignored):', c.alias, c.command
        
        self._key_file = console_config._key_file

    def init_proxies(self):
        """Initialize proxies."""
        # Map from address to process manager proxy
        address_dict = {}
        for rc in self._remote_commands:
            if rc.address not in address_dict:
                address_dict[rc.address] = ProcMgrProxy(rc.address,
                                                        rc.user_name,
                                                        self._key_file)
            
            if not address_dict[rc.address].add_remote_command(rc):
                print ('Duplicated alias <%s>. Remote command: %s' % 
                    (rc.alias, rc.command))
        self._process_managers = address_dict.values()

    def run(self):
        """Lanch interactive prompt and wait for user's command."""
        while not self._done:
            in_command = raw_input('> ')
            print in_command
            if in_command == 'exit':
                self.async_run_all(ProcMgrProxy.close)
                self._done = True
            elif in_command == 'con':
                self.connect_all()
            elif in_command == 'setup':
                self.async_run_all(ProcMgrProxy.set_up)
            elif in_command == 'run':
                # Auto connect.
                if not self.connect_all():
                    continue
                # Run command in phases.
                s = set([c.phase for c in self._remote_commands])
                phases = list(s)
                phases.sort()
                for p in phases:
                    print '\n', '=' * 20, "Current phase:", p, '=' * 20
                    start_method = lambda pm: pm.start_run(p)
                    self.async_run_all(start_method, ProcMgrProxy.run_done)
                    if not console_config._phase_check(p):
                        print "Error in phase_check, break."
                        break
            elif in_command == 'show':
                # Print setup
                print '\n', '=' * 20, "SETUP", '=' * 20
                print "Downloads:\n", console_config._remote_resource_downloads
                print "\nScripts:\n", console_config._setup_scripts
                
                # Print phases
                phases = [(c.phase, c) for c in self._remote_commands]
                phases.sort()
                cur_phase = -1                
                for p in phases:
                    if p[0] != cur_phase:
                        print '\n', '=' * 20, "Phase:", p[0], '=' * 20
                        cur_phase = p[0]
                    print p[1].alias, ":", p[1].address, p[1].command
            elif in_command == 'stop':
                # Auto connect.
                if not self.connect_all():
                    continue
                self.async_run_all(ProcMgrProxy.stop)
            elif in_command == 'shutdown':
                # Auto connect.
                if not self.connect_all():
                    continue
                # Shutdown
                self.async_run_all(ProcMgrProxy.start_shutdown,
                                   ProcMgrProxy.shutdown_done)
            elif in_command == 'close':
                self.async_run_all(ProcMgrProxy.close)
            elif in_command == 'collect':
                # Remove all existing logs.
                log_path = console_config._log_path
                if os.path.exists(log_path):
                    file_list = os.listdir(log_path)
                    for f in file_list:
                        os.remove(os.path.join(log_path, f))

                self.async_run_all(ProcMgrProxy.collect_log)
            elif in_command == 'clean':
                self.async_run_all(ProcMgrProxy.clean_all)
            elif in_command == 'terminate':
                if console_config._provisioner is not None:
                    console_config._provisioner.terminate_all()
                else:
                    print "No provisioner."
            elif in_command == 'help':
                Console._print_help()
            elif re.match(r"^e\s+(?P<fun>.*)$", in_command):
                m = re.match(r"^e\s+(?P<fun>.*)$", in_command)
                try:
                    getattr(console_config, m.group('fun'))()
                except AttributeError as e:
                    print e
            else:
                print 'unknown command:', in_command

    def connect_all(self):
        """Connect to all process managers.

        Return:
            Whether connection are successful.
        """
        if not all(p.is_connected() for p in self._process_managers):
            print 'Connecting...'
            self.async_run_all(ProcMgrProxy.connect)
        # Check failure.
        success = all(p.is_connected() for p in self._process_managers)
        if not success:
            print "Perhaps you should run 'setup' first"
        return success

    def async_run_all(self, start_method, callback_method=None):
        """Send commmand to all process managers and wait for response
        in any given order.

        After commands are sent sequentially, they are running on remote
        machines in parallel. We use select() to get responses without blocking.

        Args:
            start_method: Class method of ProcMgrProxy which runs on each proxy
                to send commands to remote process manager.
                If start_method has done the job and callback is not needed,
                True will be return, so we don't consider it then. Otherwise,
                either False or None is returned.
            callback_method: Class method of ProcMgrProxy which will run when
                the corresponding socket is ready for reading response. None
                means no response.

                *IMPORTANT* Only if the callback_method returns True, meaning
                callback is done, we remove it from next select(), so that the
                loop could stop.
        """
        # start_method returns False or None when it is not done.
        active_pms = [pm for pm in self._process_managers
                      if not start_method(pm)]

        if callback_method is not None:
            while active_pms:
                # Select on read list with timeout.
                (rlist, _, _) = select.select(active_pms, [], [], 10)
                for pm in rlist:
                    if callback_method(pm):
                        active_pms.remove(pm)

    @staticmethod
    def _print_help():
        """Print usage help."""
        print "Usage:"
        print ("1. setup. Copy files to remote cluster and lanch process"
               " managers.")
        print "2. con. Establish connections to every process manager."
        print "3. run. Run binaries on remote machines."
        print "4. stop. Terminate binaries running in process manager."
        print "5. close. Close sockets to process manager."
        print ("6. shutdown. Stop binaries, shutdown process managers and"
               " close sockets.")
        print ("7. e <function_name>. Execute function defined in"
               " command_config.")
        print


def escape(s, pattern=r'(\W)'):
    """Escape string as one argument in bash.

    Default behavior is to add prefix escape character to any non-letter
    character. If the pattern is ' ', this function can be used to escape
    spaces in a file path.

    Parameter:
        pattern: (str) The pattern of substring needed to be escaped.
    """
    r = re.compile(pattern)
    return r.subn(r'\\\1', s)[0]

def main():
    """Main function that runs console to interact with user."""
    # Parse arguments.
    parser = argparse.ArgumentParser(description='Console for cluster test.')
    parser.add_argument('-c', dest='command_config_file',
        default='command_config',
        help='set command config file path (default=command_config)')
    args = parser.parse_args()

    console = Console()
    console.config(args.command_config_file)
    console.init_proxies()
    console.run()

if __name__ == '__main__':
    main()


