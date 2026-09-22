"""Synthetic sealed-ZIP peer for C transport tests; no admission authority."""
import array
import fcntl
import os
import socket
import struct
import time


def main():
    channel=socket.socket(fileno=4)
    request=channel.recv(8192)
    magic,profile,_reserved,_zero,_timeout=struct.unpack_from('<4sBBHI',request)
    assert magic==b'KCI1'
    mode=request[12:].decode().removeprefix('/')
    if mode=='slow':time.sleep(60)
    if mode=='exit':return 9
    if mode=='privacy':
        # Exactly the fixed locale/path plus the three receipt-root variables
        # kilix-license reads; XDG_STATE_HOME and every loader/Python variable stay out.
        environment=dict(os.environ); environment.pop('LC_CTYPE',None)
        assert environment=={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
            'KILIX_LICENSE_RECEIPTS':'/ipc/receipts','GPU_TERMINAL_HOME':'/ipc/stack','HOME':'/ipc/home'}
    if mode=='privacy-empty':
        # Empty values cross as they are, and an unset one stays unset, so
        # receipt_store_root() reads the caller's own answer (an empty HOME is "/").
        environment=dict(os.environ); environment.pop('LC_CTYPE',None)
        assert environment=={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
            'KILIX_LICENSE_RECEIPTS':'','HOME':''}
    if mode in ('privacy','privacy-empty'):
        try:os.fstat(60)
        except OSError:pass
        else:return 8
    count=9 if profile==1 else 4
    if mode=='few':count-=1
    if mode=='many':count=70
    descriptors=[]
    try:
        for _i in range(count):
            writer=os.memfd_create('synthetic-ipc-only',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
            os.fchmod(writer,0o600)
            os.write(writer,b'fixture descriptor')
            fcntl.fcntl(writer,1033,0 if mode=='unsealed' else 15)
            if mode=='writable':
                descriptors.append(writer)
            else:
                descriptors.append(os.open(f'/proc/self/fd/{writer}',os.O_RDONLY|os.O_CLOEXEC))
                os.close(writer)
        response=struct.pack('<4sBBHI',b'KCO1',profile,9 if profile==1 else 4,0,0)
        if mode=='wrong-profile':response=struct.pack('<4sBBHI',b'KCO1',3-profile,count,0,0)
        if mode=='error':response=struct.pack('<4sBBHI',b'KCO1',profile,count,0,2)
        if mode=='reserved':response=struct.pack('<4sBBHI',b'KCO1',profile,count,1,0)
        if mode=='short':response=response[:-1]
        if mode=='long':response+=b'x'
        if mode=='empty-first':channel.send(b'')
        control=[] if mode=='fdless' else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))]
        channel.sendmsg([response],control)
        if mode=='extra-empty':channel.send(b'')
        if mode=='extra-rights':channel.sendmsg([b''],control)
        if mode=='extra-data':channel.send(b'x')
        return 7 if mode=='late-error' else 0
    finally:
        for fd in descriptors:os.close(fd)
        channel.close()


if __name__=='__main__':raise SystemExit(main())
