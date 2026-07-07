"""
This implements resources for Twisted webservers using the WSGI
interface of Django. This alleviates the need of running e.g. an
Apache server to serve Evennia's web presence (although you could do
that too if desired).

The actual servers are started inside server.py as part of the Evennia
application.

(Lots of thanks to http://github.com/clemesha/twisted-wsgi-django for
a great example/aid on how to do this.)


"""

import urllib.parse
from urllib.parse import quote as urlquote

from django.conf import settings
from twisted.web import http, resource, server, static
from twisted.web.proxy import ReverseProxyResource
from twisted.web.server import NOT_DONE_YET


#
# X-Forwarded-For Handler
#


class HTTPChannelWithXForwardedFor(http.HTTPChannel):
    """
    HTTP xforward class

    """

    def allHeadersReceived(self):
        """
        Check to see if this is a reverse proxied connection.

        """
        if self.requests:
            CLIENT = 0
            http.HTTPChannel.allHeadersReceived(self)
            req = self.requests[-1]
            client_ip, port = self.transport.client
            proxy_chain = req.getHeader("X-FORWARDED-FOR")
            if proxy_chain and client_ip in settings.UPSTREAM_IPS:
                forwarded = proxy_chain.split(", ", 1)[CLIENT]
                self.transport.client = (forwarded, port)


# Monkey-patch Twisted to handle X-Forwarded-For.

http.HTTPFactory.protocol = HTTPChannelWithXForwardedFor


class EvenniaReverseProxyResource(ReverseProxyResource):
    def getChild(self, path, request):
        """
        Create and return a proxy resource with the same proxy configuration
        as this one, except that its path also contains the segment given by
        path at the end.

        Args:
            path (str): Url path.
            request (Request object): Incoming request.

        Return:
            resource (EvenniaReverseProxyResource): A proxy resource.

        """
        request.notifyFinish().addErrback(
            lambda f: 0
            # lambda f: logger.log_trace("%s\nCaught errback in webserver.py" % f)
        )
        return EvenniaReverseProxyResource(
            self.host, self.port, self.path + "/" + urlquote(path, safe=""), self.reactor
        )

    def render(self, request):
        """
        Render a request by forwarding it to the proxied server.

        Args:
            request (Request): Incoming request.

        Returns:
            not_done (char): Indicator to note request not yet finished.

        """
        # RFC 2616 tells us that we can omit the port if it's the default port,
        # but we have to provide it otherwise
        request.content.seek(0, 0)
        qs = urllib.parse.urlparse(request.uri)[4]
        if qs:
            rest = self.path + "?" + qs.decode()
        else:
            rest = self.path
        rest = rest.encode()
        clientFactory = self.proxyClientFactoryClass(
            request.method,
            rest,
            request.clientproto,
            request.getAllHeaders(),
            request.content.read(),
            request,
        )
        clientFactory.noisy = False
        self.reactor.connectTCP(self.host, self.port, clientFactory)
        # don't trigger traceback if connection is lost before request finish.
        request.notifyFinish().addErrback(lambda f: 0)
        # request.notifyFinish().addErrback(
        #   lambda f:logger.log_trace("Caught errback in webserver.py: %s" % f)
        return NOT_DONE_YET


#
# Site with deactivateable logging
#


class Website(server.Site):
    """
    This class will only log http requests if settings.DEBUG is True.
    """

    noisy = False

    def logPrefix(self):
        "How to be named in logs"
        if hasattr(self, "is_portal") and self.is_portal:
            return "Webserver-proxy"
        return "Webserver"

    def log(self, request):
        """Conditional logging"""
        if settings.DEBUG:
            server.Site.log(self, request)


class PrivateStaticRoot(static.File):
    """
    This overrides the default static file resource so as to not make the
    directory listings public (that is, if you go to /media or /static you
    won't see an index of all static/media files on the server).

    """

    def directoryListing(self):
        return resource.ForbiddenResource()
