#!/usr/bin/python -u
#
# Very early prototype of apt-transport-spacewalk
#
# Author:  Simon Lukasik <xlukas08 [at] stud.fit.vutbr.cz>
# Date:    2011-01-01
# License: GPLv2
#
#

import sys
import hashlib

import warnings
warnings.filterwarnings("ignore", message="the md5 module is deprecated; use hashlib instead")
sys.path.append("/usr/share/rhn/")

from httplib import HTTPConnection, HTTPSConnection
from up2date_client import config
from up2date_client import rhnChannel
from up2date_client import up2dateAuth
from up2date_client import up2dateErrors



class pkg_acquire_method:
    """
    This is slightly modified python variant of apt-pkg/acquire-method.
    It is a skeleton class that implements only very basic of apt methods
    functionality.
    """
    __eof = False

    def __init__(self):
        print "100 Capabilities\nVersion: 1.0\nSingle-Instance: true\n\n",

    def __get_next_msg(self):
        """
        Apt uses for communication with its methods the text protocol similar
        to http. This function parses the protocol messages from stdin.
        """
        if self.__eof:
            return None
        result = {};
        line = sys.stdin.readline()
        if not line:
            self.__eof = True
            return None
        s = line.split(" ", 1)
        result['_number'] = int(s[0])
        result['_text'] = s[1].strip()

        while not self.__eof:
            line = sys.stdin.readline()
            if not line:
                self.__eof = True
                return result
            if line == '\n':
                return result
            s = line.split(":", 1)
            result[s[0]] = s[1].strip()

    def __dict2msg(self, msg):
        """Convert dictionary to http like message"""
        result = ""
        for item in msg.keys():
            if msg[item] != None:
                result += item + ": " + msg[item] + "\n"
        return result

    def status(self, **kwargs):
        print "102 Status\n%s\n" % self.__dict2msg(kwargs),

    def uri_start(self, msg):
        print "200 URI Start\n%s\n" % self.__dict2msg(msg),

    def uri_done(self, msg):
        print "201 URI Done\n%s\n" % self.__dict2msg(msg),

    def uri_failure(self, msg):
        print "400 URI Failure\n%s\n" % self.__dict2msg(msg),

    def run(self):
        """Loop through requests on stdin"""
        while True:
            msg = self.__get_next_msg()
            if msg == None:
                return 0
            if msg['_number'] == 600:
                # TODO: catch exceptions
                self.fetch(msg)
            else:
                return 100




def get_ssl_ca_cert(up2date_cfg):
    if not (up2date_cfg.has_key('sslCACert') and up2date_cfg['sslCACert']):
       raise BadSslCaCertConfig

    ca_certs = up2date_cfg['sslCACert']
    if type(ca_certs) == list:
        return ca_certs



class spacewalk_method(pkg_acquire_method):
    """
    Spacewalk acquire method
    """
    up2date_cfg = None
    server_fqdn = None
    login_info = None
    current_url = None
    svr_channels = None
    http_headers = None
    base_channel = None
    conn = None

    def fail(self, message = 'This system is not registered with the spacewalk server'):
        self.uri_failure({'URI': self.uri,
                          'Message': message})
        return False


    def __load_config(self):
        if self.up2date_cfg == None:
            self.up2date_cfg = config.initUp2dateConfig()
            self.server_fqdn = self.up2date_cfg['serverURL'].split("/",3)[2]
        # TODO: proxy settings


    def __login(self):
        if self.login_info == None:
            self.status(URI = self.uri, Message = 'Logging into the spacewalk server')
            self.login_info = up2dateAuth.getLoginInfo() # TODO: catch exceptions
            if not self.login_info:
                return self.fail()
            self.status(URI = self.uri, Message = 'Logged in')
        return True


    def __init_channels(self):
        if self.svr_channels == None:
            self.svr_channels = rhnChannel.getChannelDetails() # TODO: catch exceptions
            # TODO: some conduit magic here (or rather not)
            # TODO CHANNELS
            # sslcacert = get_ssl_ca_cert(self.up2date_cfg) # TODO: to remove or not to remove
            for channel in self.svr_channels:
                if channel['parent_channel'] == '':
                    self.base_channel = channel['label']
                # TODO: Full RhnRepo


    def __init_headers(self):
        if self.http_headers == None:
            rhn_needed_headers = ['X-RHN-Server-Id',
                                  'X-RHN-Auth-User-Id',
                                  'X-RHN-Auth',
                                  'X-RHN-Auth-Server-Time',
                                  'X-RHN-Auth-Expire-Offset']
            self.http_headers = {};
            for header in rhn_needed_headers:
                if not self.login_info.has_key(header):
                    return self.fail("Missing required login information %s" % (header))
                self.http_headers[header] = self.login_info[header]
            self.http_headers['X-RHN-Transport-Capability'] = 'follow-redirects=3'
        return True


    def __make_conn(self):
        # TODO: HTTPS + certificate verification
        if self.conn == None:
            self.conn = HTTPConnection(self.server_fqdn)


    def __transform_document(self, document):
        """Transform url given by apt to real spacewalk url"""
        document = document.replace('dists/channels:/main/', 
                'dists/channels:/' + self.base_channel  + '/', 1)
        document = document.replace('binary-i386', 'repodata', 1) # TODO
        document = document.replace('dists/channels:/', '/XMLRPC/GET-REQ/', 1)
        return document


    def fetch(self, msg):
        """
        Fetch the content from spacewalk server to the file.

        Acording to the apt protocol msg must contain: 'URI' and 'Filename'.
        Other possible keys are: 'Last-Modified', 'Index-File', 'Fail-Ignore'
        """
        self.uri = msg['URI']
        self.filename = msg['Filename']

        self.__load_config()
        server_fqdn, document = self.uri.split("/",3)[2:]
        if server_fqdn != self.server_fqdn:
            return self.fail()

        if not self.__login():
            return

        self.__init_channels()

        document = self.__transform_document(document)

        if not self.__init_headers():
            return

        self.__make_conn()

        self.conn.request("GET", "/" + document, headers = self.http_headers)
        self.status(URI = self.uri, Message = 'Waiting for headers')

        res = self.conn.getresponse()
        if res.status != 200:
            self.uri_failure({'URI': self.uri,
                              'Message': str(res.status) + '  ' + res.reason,
                              'FailReason': 'HttpError' + str(res.status)})
            while True:
                data = res.read(4096)
                if not len(data): break
            return

        self.uri_start({'URI': self.uri,
                        'Size': res.getheader('content-length'),
                        'Last-Modified': res.getheader('last-modified')})

        f = open(self.filename, "w")
        hash_sha256 = hashlib.sha256()
        hash_md5 = hashlib.md5()
        while True:
            data = res.read(4096)
            if not len(data):
                break
            hash_sha256.update(data)
            hash_md5.update(data)
            f.write(data)
        f.close()

        self.uri_done({'URI': self.uri,
                       'Filename': self.filename,
                       'Size': res.getheader('content-length'),
                       'Last-Modified': res.getheader('last-modified'),
                       'MD5-Hash': hash_md5.hexdigest(),
                       'MD5Sum-Hash': hash_md5.hexdigest(),
                       'SHA256-Hash': hash_sha256.hexdigest()})


    def __del__(self):
        if self.conn:
            self.conn.close()



if __name__ == '__main__':
    method = spacewalk_method()
    ret = method.run()
    sys.exit(ret)

