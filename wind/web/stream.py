"""

    wind.web.stream
    ~~~~~~~~~~~~~~~

    Provides models for handling socket io stream.

"""

import errno
import socket
from wind.looper import Looper
from wind.poll import PollEvents
from wind.exceptions import StreamError
from wind.web.datastructures import FlexibleDeque
from wind.socketserver import EWOULDBLOCK, ECONNRESET


class StreamBuffer(FlexibleDeque):
    """Buffer for stream read and write."""

    def __init__(self, *args, **kwargs):
        """Initialize `_frozen` to False. 
        `_frozen` is flag used to check whether current buffer 
        is available for reading from or writing to.

        """
        self._frozen = False

    @property
    def frozen(self):
        return self._frozen

    @frozen.setter
    def frozen(self, value):
        self._frozen = value


class BaseStream(object):
    """Base class for io stream classes.
    Provide methods to read from and write to file or socket.
    This class can handle read, write methods asynchronously
    by attaching callback when calling method.

    Methods for the caller:

    - __init__(chunk_size=4096)
    - open()
    - close()
    - read_bytes(num_bytes)
    - read_until(delimiter)
    - write(chunk)
    
    Methods should be overrided

    - _read_from_fd()
    - _write_to_fd(chunk)


    """

    def __init__(self, looper=None, chunk_size=4096):
        """Initialize and open base stream.
        
        @param chunk_size : chunk size for read.

        """
        self._looper = looper or Looper.instance()
        self._read_buffer = StreamBuffer() 
        self._write_buffer = StreamBuffer() 
        self._read_chunk_size = chunk_size
        self._write_chunk_size = 128 * 1024
        self._read_buffer_bytes = 0
        self._is_opened = False 

        # Stream should save this flags because read, write should be started
        # with last states when read, write was excuted by event handler.
        self._bytes_to_read = None
        self._delimiter = None

        # Saves asynchronous event handled by `looper`. (PollEvents)
        self._handler_event = None

        self._read_callback = None
        self._write_callback = None
        self._close_callback = None

        self.open()
    
    def open(self):
        self._is_opened = True

    def close(self):
        if not self.closed:
            if self._handler_event is not None:
                slef._handler_event = None
                self._looper.remove_handler(self.fileno())
            self._is_opened = False
            self._close_fd()
            self._run_close_callback()
    
    def _close_fd(self):
        raise NotImplementedError

    def fileno(self):
        """Returns fd of socket or file"""
        raise NotImplementedError

    @property
    def closed(self):
        return not self._is_opened
    
    @property
    def reading(self):
        return self._read_callback is not None

    @property
    def writing(self):
        return self._write_callback is not None
    
    def read_bytes(self, bytes_to_read, callback):
        """Read `bytes_to_read` bytes from file"""
        if not isinstance(bytes_to_read, int):
            raise StreamError('`read_bytes` can only accept `int` param')
        self._bytes_to_read = bytes_to_read

        self._add_callback(callback)
        self._process_read()
    
    def read_until(self, delimiter, callback):
        """Read until first occurrence of `delimiter`.
        Returned chunk that contains `delimiter`
        
        """
        if not isinstance(delimiter, basestring):
            raise StreamError('`read_until` can only accept `str` param')
        self.delimiter = delimiter

        self._add_callback(callback)
        self._process_read()

    def _process_read(self):
        """fd -> read buffer -> memory"""
        while not self.closed:
            if self._to_read_buffer() == 0:
                # End of read
                break
        self._read()

    def _to_read_buffer(self):
        """Read chunk from socket or file and returns number of bytes read.
        
        """
        try:
            chunk = self._read_from_fd()
            if not chunk or chunk is None:
                return 0
        except socket.error as e:
            self.close()

        # No buffer size limit yet.
        self._read_buffer.append(chunk)
        self._read_buffer_bytes += len(chunk)
        return len(chunk)

    def _read(self):
        """Read chunk from `_read_buffer` and run callback with that chunk.

        """
        self._raise_if_closed()

        # XXX: handle all expectable cases
        read_bytes = 0
        if self._bytes_to_read is not None:
            read_bytes = min(self._bytes_to_read, self._read_buffer_bytes)
            self._run_callback(
                self._pop_callback(), self._pop_chunk(read_bytes))
        
        if self._delimiter is not None:
            while True:
                pos = self._read_buffer[0].find(self.delimiter)
                if pos != -1:
                    # Found delimiter
                    self._run_callback(
                        self._pop_callback(), 
                        self._pop_chunk(pos + len(self.delimiter)))
                
                if len(self._read_buffer) == 1:
                    # No delimiter found in whole read buffer.
                    break

                # No delimiter found in first chunk.
                self._read_buffer.gather(
                    len(self._read_buffer[0] + self._read_buffer[1]))
            
    
    def _pop_chunk(self, read_bytes):
        """Pop chunk from `_read_buffer` and Returns chunk."""
        self._read_buffer_bytes -= read_bytes
        self._read_buffer.gather(read_bytes)
        return self._read_buffer.popleft()

    def _read_from_fd(self):
        raise NotImplementedError()
    
    def write(self, chunk, callback):
        if not isinstance(chunk, basestring):
            raise StreamError('Can write only chunk of `bytes`')

        self._add_callback(callback, read=False)
        self._process_write(chunk)
    
    def _process_write(self, chunk, partial=False):
        self._raise_if_closed()

        if not partial:
            self._to_write_buffer(chunk)

        while self._write_buffer:
            try:
                num_bytes = self._write_to_fd(self._write_buffer[0])
                if num_bytes == 0:
                    self._write_buffer.frozen = True
                    break
                self._write_buffer.frozen = False

                # Partial writing is handled here.
                self._write_buffer.gather(num_bytes)
                self._write_buffer.popleft()
            except socket.error as e:
                if e.args[0] in EWOULDBLOCK:
                    # Freeze
                    self._write_buffer.frozen = True
                elif e.args[0] in ECONNRESET:
                    self.close()
                else:
                    raise StreamError(e)
                break

        # Post write process.
        if self._write_buffer and not partial:
            # Writing is not completed at one go
            self._attach_stream_handler(PollEvents.WRITE)
        elif not self._write_buffer:
            self._run_callback(self._pop_callback(read=False))

    def _to_write_buffer(self, chunk):
        """Fill `_write_buffer` after dividing `chunk` with 
        `_write_chunk_size` bytes blocks.
        
        """
        
        if chunk:
            for i in range(0, len(chunk), self._write_chunk_size):
                self._write_buffer.append(chunk[0:i + self._write_chunk_size])

    def _write_to_fd(self, chunk):
        raise NotImplementedError()
    
    def _raise_if_closed(self):
        if self.closed:
            raise StreamError('Cannot read because stream is already closed')
       
    def _pop_callback(self, read=True):
        """Returns saved callback and clear read or write callback

        @param read (optional): True if read callback else write callback.
        """
        if read:
            callback = self._read_callback
            self._read_callback = None
        else:
            callback = self._write_callback
            self._write_callback = None
        return callback

    def _add_callback(self, callback, read=True):
        """Check whether passed callback has acceptable form.
        We imported `inspect` here because it's not on the mainstream.
        
        @param callback: Callback method to be saved.
        @param read (optional): True if read callback else write callback.
        """
        from inspect import getargspec
        if callback is None:
            return

        if not hasattr(callback, '__call__'):
            raise StreamError('Stream callback is not callable')

        spec = getargspec(callback)
        if not spec.args:
            raise StreamError(
                'Should provide room for chunk in stream callback')
        
        if read:
            self._read_callback = callback
        else:
            self._write_callback = callback

    def _run_callback(self, callback, *args):
        """Immediately run saved callback"""
        try:
            callback(*args)
        except Exception as e:
            self.close()
            raise StreamError(e)
    
    def _run_close_callback(self):
        if self.closed:
            if self._close_callback is not None:
                callback = self._close_callback
                self._run_callback(callback)
                self._close_callback = None
                self._read_callback = self._write_callback = None

    def event_handler(self, fd, events):
        """Handler which will attached to `looper`"""
        try:
            if events & PollEvents.READ:
                self._handle_read()

            if events & PollEvents.WRITE:
                self._handle_write()
            
            if events & PollEvents.ERROR:
                # TODO: Should handle PollEvents.ERROR
                pass

            if self.closed:
                return

        except Exception as e:
            raise StreamError(e)

    def _handle_write(self):
        """Handle write process when fd is available.
        This method will be passed to event handler of `looper`
        
        """
        try:
            self._process_write()
        except Exception:
            self.close()

    def _handle_read(self):
        """Handle read process when fd is available.
        This method will be passed to event handler of `looper`
        
        """
        try:
            self._process_read()
        except Exception:
            self.close()
    
    def _attach_stream_handler(self, event_mask):
        """Attach handler to `looper` for the purpose of handling
        asynchronous reading and writing"""
        if self._handler_event is None:
            # Attach new handler
            self._handler_event = event_mask | PollEvents.ERROR
            self._looper.attach_handler(
                self.fileno(), self._handler_event, self.event_handler)
        elif not self._handler_event & event_mask:
            # Update event of existing handler
            self._handler_event |= event_mask
            self._looper.update_handler(self.fileno(), event_mask)
 

class SocketStream(BaseStream):
    def __init__(self, socket, *args, **kwargs):
        if not isinstance(socket, socket.socket):
            raise StreamError(
                'SocketStream can only be initialized with `socket.socket`')
        self.socket = socket
        super(SocketStream, self).__init__(*args, **kwargs)
    
    def fileno(self):
        return self.socket.fileno()

    def _read_from_fd(self):
        try:
            chunk = self.socket.recv(self._read_chunk_size)
            if not chunk:
                # Should close stream here because nothing is left to be read.
                self.close()
                return None
            return chunk
        except socket.error as e:
            if e.args[0] not in EWOULDBLOCK:
                raise
            return None

    def _write_to_fd(self, chunk):
        self.socket.send(chunk)

    def _close_fd(self):
        self.socket.close()
        slef.socket = None


class FileStream(BaseStream):
    def __init__(self, file_, *args, **kwargs):
        if not isinstance(file_, file):
            raise StreamError(
                'FileStream can only be initialized with `file`')
        self.file_ = file_
        super(FileStream, self).__init__(*args, **kwargs)
    
    def fileno(self):
        return self.file_.fileno()

    def _read_from_fd(self):
        return self.file_.read(self._read_chunk_size)
            
    def _write_to_fd(self, chunk):
        try:
            self.file_.write(chunk)
        except IOError as e:
            raise StreamError(e)
    
    def _close_fd(self):
        self.file_.close()
        self.file_ = None

