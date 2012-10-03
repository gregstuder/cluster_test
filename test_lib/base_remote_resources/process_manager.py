#!/usr/bin/python
"""Process manager runs on a remote machine, receives commands from console,
manages processes and relanch processes when they crash.
"""

import os
import re
import select
import shlex
import socket
import subprocess
import sys
import threading
import time

class LineReader(object):
    """Wrap a socket for reading lines."""

    def __init__(self, read_socket):
        """Wrap read_socket for reading lines.

        Note that the read_socket is not owned here and should be closed
        outside.
        """
        self._socket = read_socket
        # self._line_buffer buffers incomplete line.
        self._line_buffer = ''

    def read_lines(self):
        """Non-blocking read lines from the socket.

        It is used after select() notifies that the socket is ready for read.

        Returns:
            A list of lines just read, usually only containing one line. If
            the data read so far does not compose a whole line, the incomplete
            line is buffered and an empty list will be returned.

            End of File(EOF) will return None.
        """
        # If there is more data than 4096 bytes, select() will notify again.
        data = self._socket.recv(4096)
        if data == '': return None  # EOF

        lines = []
        self._line_buffer += data
        pos = self._line_buffer.find('\n')
        while pos >= 0:  # Loop to find all lines.
            lines.append(self._line_buffer[:pos+1])
            self._line_buffer = self._line_buffer[pos+1:]
            pos = self._line_buffer.find('\n')
        return lines


class Monitor(threading.Thread):
    """Monitor of a process.

    A Monitor is a thread that forks a new process to run a given command,
    reports states of the process to the Manager, relanches the process if
    it dies and is waiting for its finish.

    Attributes:
        command: (List of str) The list of the program to execute and its
            arguments after split according to shell style.
            For example: ['ls', '-l'].
            The present working directory is added to $PATH, so that program
            in pwd could run without './'.
        alias: Name of monitor, unique in a Manager, used when stopping
            the process and the Monitor.
        in_r, in_w: The read and write file descriptors of input of process.
        out_r, out_w: The read and write file descriptors of output of process.
    """
    # Commands used between Monitor and Manager.
    READY = 'ready\n'
    LANCH = 'lanch\n'
    LANCHED = 'lanched\n'
    RELANCH = 'relanch\n'
    DIED = 'died\n'
    FINISHED = 'finished\n'

    def __init__(self, command_str, alias=None):
        """Arguments to __init__() are as described in the description above."""
        # Initialize Thread before start().
        super(Monitor, self).__init__()

        # Split command for shell.
        args = shlex.split(command_str)
        self.command = args

        # Create pipes and system will clean them up after corresponding
        # process finishes.
        (pipe_r, pipe_w) = os.pipe()
        self.in_r = os.fdopen(pipe_r, 'r', 0)  # No buffering.
        self.in_w = os.fdopen(pipe_w, 'w', 0)  # No buffering.
        (pipe_r, pipe_w) = os.pipe()
        self.out_r = os.fdopen(pipe_r, 'r', 0)
        self.out_w = os.fdopen(pipe_w, 'w', 0)

        # The flag indicating whether the Monitor thread should stop.
        self._done = False
        # The lock to protect slow popen() running from stop().
        self._lanch_lock = threading.Lock()
        self._process = None  # The underlying process
        if alias is None:
            self.alias = self.command[0]  # The name of binary to be excuted.
        else:
            self.alias = alias

        # The state of moniter that console is interested in when starting
        # the process.
        self.interest = Monitor.LANCHED

    def fileno(self):
        """File number used by select()."""
        return self.out_r.fileno()

    def run(self):
        """Main method of the thread.

        Run until underlying process finishes or stop() is called.
        If underlying process terminates, relanch it.
        """
        while not self._done:
            # Ready. Notify Manager.
            self.out_w.write(Monitor.READY)

            # Lanch. Wait for Manager's command.
            if self.in_r.readline() != Monitor.LANCH:
                return

            log_file_name = self.alias + '_proc.log'
            if os.path.exists(log_file_name):
                # Rename existing file.
                new_file_name = "%s_%f" % (log_file_name, time.time())
                while os.path.exists(new_file_name):
                    new_file_name = "%s_%f" % (log_file_name, time.time())
                os.rename(log_file_name, new_file_name)

            log_file = open(log_file_name, 'w')
            with self._lanch_lock:
                # Check _done flag under lock protection.
                if self._done: return
                # Add current dir to $PATH.
                env = os.environ.copy()
                env["PATH"] = os.getcwd() + ':' + env["PATH"]
                # Fork a process with given envirenment.
                self._process = subprocess.Popen(
                    self.command, stdin=subprocess.PIPE, stdout=log_file,
                    stderr=log_file, env=env, close_fds=True)

            # Lanching. Notify Manager.
            self.out_w.write(Monitor.LANCHED)
            # Wait for the process finishes or terminates and buffer
            # all output in memory.
            output, err = self._process.communicate()
            print output
            print err
            log_file.close()

            # Finished or died. Notify Manager.
            if self._done:
                return  # Stopped.
            elif self._process.returncode == 0:
                self.out_w.write(Monitor.FINISHED)
                return
            else:
                self.out_w.write(Monitor.DIED)

            # Relanch. Wait for Manager's command
            if self.in_r.readline() != Monitor.RELANCH:
                return

    def stop(self):
        """Terminate underlying process and stop Monitor."""
        if self._done: return # Already done.
        print self.alias, "will terminate()"
        self._done = True
        with self._lanch_lock:
            # Check if the process is still running.
            if self._process is not None and self._process.returncode is None:
                # We wrap terminate() in try/except block because there is a
                # chance that the process just finished after the above check.
                # We have no other way to know whether terminate() is
                # successful or not.
                try:
                    self._process.terminate()
                except OSError:
                    print 'Catched OSError in', self.alias


class ConsoleSocket(object):
    """Wrap socket and LineReader to connect with Console,
    used in select() by the Process Manager.
    """

    def __init__(self, console_socket):
        """Wrap console_socket.

        The console_socket is owned here.
        We need to close it after use or when socket on console side closed.
        """
        self._socket = console_socket
        self._reader = LineReader(console_socket)

    def fileno(self):
        """File number used by select()."""
        return self._socket.fileno()

    def read_commands(self):
        """Read lines from socket."""
        return self._reader.read_lines()

    def send_all(self, data):
        """Send data to socket until either all data has been sent or
        an error occurs.
        """
        self._socket.sendall(data)

    def close(self):
        """Close underlying socket and clean up reader."""
        self._reader = None
        self._socket.close()
        self._socket = None


class ProcessManager(object):
    """Agent running on a remote machine acts as a server to run commands
    sent from console.

    Attributes:
        port: (int) The port used for listening connections from Console.
    """
    # Commands used between Console and Manager as a part of protocol.
    RUN = 'run'
    STOP = 'stop'
    OK = 'ok\n'
    DUP_ALIAS = 'duplicated alias\n'
    SHUTDOWN = 'shutdown\n'

    def __init__(self, port):
        self.port = port
        # List of monitors. Each one corresponds to a process.
        self._monitors = []
        # ConsoleSocket. Only one console could connect to a Manager.
        self._console_socket = None
        # Flag to indicate whether the Manager should stop.
        self._done = False

    def add_monitor(self, monitor):
        """Add Monitor to list and control it through pipes.

        After added, the monitor need to be started either by
        ProcessManager.start() or by calling monitor.start() manually.

        Returns:
            If the alias of monitor already exists, return False.
            Otherwise return True.
        """
        # Check alias duplication.
        if any(monitor.alias == m.alias for m in self._monitors):
            return False
        self._monitors.append(monitor)
        return True

    def _build_server_socket(self):
        """Construct a socket and set it as a server socket."""
        # Create an INET, STREAMing socket.
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Reuse port.
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('', self.port))
        # Become a server socket.
        server_socket.listen(5)
        return server_socket

    def start(self):
        """Start the Manager, running until shutdown.

        Start Monitors added before start() and start listening on server socket
        waiting for Console to connect.
        """
        # Server socket will become ready for read when console connects,
        # so that accept() will not block.
        server_socket = self._build_server_socket()

        # Start monitors.
        for t in self._monitors:
            t.start()

        # Loop until done.
        while not self._done:
            # select() on _monitors, server_socket and _console_socket.
            potential_read_list = self._monitors[:] # Make a copy.
            potential_read_list.append(server_socket)
            if self._console_socket is not None:
                potential_read_list.append(self._console_socket)

            # Select on read list with timeout.
            (rlist, _, _) = select.select(potential_read_list, [], [], 1)

            for read_ready in rlist:
                # Monitor becomes ready for read.
                if isinstance(read_ready, Monitor):
                    self._manage_monitor(read_ready)

                # Server socket becomes ready for read, we can accept without
                # blocking.
                elif read_ready == server_socket:
                    (console_socket, _) = read_ready.accept()
                    console_socket.setblocking(0)  #  Set to non-blocking.
                    self._console_socket = ConsoleSocket(console_socket)
                    print 'console socket connected'

                # Console socket becomes ready for read.
                else:
                    commands = read_ready.read_commands()
                    if commands is None:
                        # EOF of console socket. Console closed the socket.
                        print 'EOF of console socket'
                        self._console_socket.close()
                        self._console_socket = None
                    else:
                        for command in commands:
                            print command
                            self._process_command(command)

        # Join terminated threads.
        for t in self._monitors:
            t.join()

        if self._console_socket is not None:
            self._console_socket.close()
        server_socket.close()

    def _manage_monitor(self, monitor):
        """Manage Monitor through pipes."""
        # Block if command does not terminate with '\n'. But all commands in
        # our protocol terminate with '\n', so we can use blocking read safely.
        output = monitor.out_r.readline()
        # EOF should not happen.
        if output == '':
            print "EOF of Monitor's output"

        print monitor.alias, output,

        # Communicate with Monitor.
        if output == Monitor.READY:
            monitor.in_w.write(Monitor.LANCH)
        elif output == Monitor.LANCHED:
            if monitor.interest == Monitor.LANCHED:
                response = "%s %s" % (monitor.alias, ProcessManager.OK)
                self._console_socket.send_all(response)
                monitor.interest = None
        elif output == Monitor.DIED:
            monitor.in_w.write(Monitor.RELANCH)
        elif output == Monitor.FINISHED:
            # Send OK if console is waiting for process finsih.
            if monitor.interest == Monitor.FINISHED:
                response = "%s %s" % (monitor.alias, ProcessManager.OK)
                self._console_socket.send_all(response)
                monitor.interest = None
            # Join finished thread.
            monitor.join()
            self._monitors.remove(monitor)
        else:
            print 'Unknown Monitor output'

    def _stop_monitor(self, alias):
        """Stop monitor with given alias or stop all monitors if alias is None.

        All stopped monitor will be joined at the end of main method.
        """
        if alias is None:  # Stop all.
            for m in self._monitors:
                m.stop()
            del self._monitors[:]
        else:
            for m in self._monitors:
                if m.alias == alias:
                    m.stop()
                    # We only touch one element, so it would be safe to remove
                    # it when iterating.
                    self._monitors.remove(m)
                    return
            print alias, 'does not exist'

    def _process_command(self, command):
        """Process command sent from Console"""
        # STOP
        if command.startswith(ProcessManager.STOP):
            self._stop_monitor(ProcessManager._parse_stop_command(command))
        # SHUTDOWN
        elif command == ProcessManager.SHUTDOWN:
            self._stop_monitor(None)
            self._done = True  # Stop Manager.
        # RUN
        elif command.startswith(ProcessManager.RUN):
            alias, command, wait = ProcessManager._parse_run_command(command)
            monitor = Monitor(command, alias)
            if wait:
                monitor.interest = Monitor.FINISHED
            if self.add_monitor(monitor):
                # No alias duplication.
                monitor.start()  # Start Monitor as a new thread.
            else:
                # Alais duplicate. Report to Console.
                response = "%s %s" % (monitor.alias, ProcessManager.DUP_ALIAS)
                self._console_socket.send_all(response)
        # Unknown
        else:
            print 'Unknown command'

    @staticmethod
    def _parse_run_command(command_str):
        """Paser RUN command "run [-as <alias>][-w] <program and arguments>".

        -as <alias>: Give the command an alias.
        -w: Wait for the command to stop.

        For example:
            "run ls -l", "run -as mongod01 mongod --dbpath /var/lib/mongodb/"
            "run -c mkdir -p ./data"

        Returns:
            The tuple (alias, command).
        """
        m = re.match(r"^run(\s+-as\s+(?P<alias>[^\s]*))?(\s+(?P<w>-w))?\s+(?P<cmd>.*)\n$",
                     command_str)
        return m.group('alias'), m.group('cmd'), m.group('w')

    @staticmethod
    def _parse_stop_command(command_str):
        """Paser STOP command "stop <alias>".

        For example: "stop ls", "stop mongod01"

        Returns:
            Alias is returned.
        """
        m = re.match(r"^stop(\s+(?P<alias>.*))?\n$", command_str)
        return m.group('alias')


def main():
    """Main method that starts a process manager at a given port."""
    port = 2900
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    manager = ProcessManager(port)
    manager.start()

if __name__ == '__main__':
    main()

