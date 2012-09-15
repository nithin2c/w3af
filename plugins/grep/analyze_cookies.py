'''
analyze_cookies.py

Copyright 2006 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

'''
import Cookie
import re

import core.controllers.outputManager as om
import core.data.kb.knowledgeBase as kb
import core.data.kb.info as info
import core.data.kb.vuln as vuln
import core.data.constants.severity as severity

from core.controllers.plugins.grep_plugin import GrepPlugin
from core.controllers.misc.group_by_min_key import group_by_min_key


class analyze_cookies(GrepPlugin):
    '''
    Grep every response for session cookies sent by the web application.
      
    @author: Andres Riancho (andres.riancho@gmail.com)
    '''

    COOKIE_HEADERS = ('set-cookie', 'cookie', 'cookie2')

    COOKIE_FINGERPRINT = (
            ('st8id=','Teros web application firewall'),
            ('ASINFO=','F5 TrafficShield'),
            ('NCI__SessionId=','Netcontinuum'),
                    
            # oracle
            ('$OC4J_','Oracle container for java'),
                    
            # Java
            ('JSESSIONID=','Jakarta Tomcat / Apache'),
            ('JServSessionIdroot=','Apache JServ'),
                    
            # ASP
            ('ASPSESSIONID','ASP'),
            ('ASP.NET_SessionId=','ASP.NET'),
                    
            # PHP
            ('PHPSESSID=','PHP'),
                    
            # SAP
            ('sap-usercontext=sap-language=','SAP'),
                    
            # Others
            ('WebLogicSession=','BEA Logic'),
            ('SaneID=','Sane NetTracker'),
            ('ssuid=','Vignette'),
            ('vgnvisitor=','Vignette'),
            ('SESSION_ID=','IBM Net.Commerce'),
            ('NSES40Session=','Netscape Enterprise Server'),
            ('iPlanetUserId=','iPlanet'),
            ('RMID=','RealMedia OpenADStream'),
            ('cftoken=','Coldfusion'),
            ('PORTAL-PSJSESSIONID=','PeopleSoft'),
            ('WEBTRENDS_ID=','WebTrends'),
            ('sesessionid=','IBM WebSphere'),
            ('CGISESSID=','Perl CGI::Session'),
            ('GX_SESSION_ID','GeneXus'),
            ('WC_SESSION_ESTABLISHED','WSStore'),

        )

    SECURE_RE = re.compile('; *?secure([\s;, ]|$)', re.I)
    HTTPONLY_RE = re.compile('; *?httponly([\s;, ]|$)', re.I)
    

    def __init__(self):
        GrepPlugin.__init__(self)
        self._already_reported_server = []

    def grep(self, request, response):
        '''
        Plugin entry point, search for cookies.
        
        @param request: The HTTP request object.
        @param response: The HTTP response object
        @return: None
        '''
        # do this check every time
        self._ssl_cookie_via_http( request, response )
        
        #
        # Analyze the response headers and find cookies
        #
        headers = response.getHeaders()
        
        for header_name in headers:  
            if header_name.lower() in self.COOKIE_HEADERS:
                
                cookie_header_value = headers[header_name].strip()
                cookie_object = self._parse_cookie( request, response, cookie_header_value )
                
                if cookie_object is not None:
                    self._collect_cookies( request, response,
                                           cookie_object,
                                           cookie_header_value )
    
                    # Find if the cookie introduces any vulnerability,
                    # or discloses information
                    self._analyze_cookie_security( request, response, 
                                                   cookie_object,
                                                   cookie_header_value )

    def _collect_cookies(self, request, response, cookie_object, cookie_header_value):
        '''
        Store (unique) cookies in the KB for later analysis.
        '''
        for cookie_info in kb.kb.get( self, 'cookies' ):
            stored_cookie_obj = cookie_info['cookie-object']
            # Cookie class has an __eq__ which compares Cookies' keys for
            # equality, not the values, so these two cookies are equal:
            #        a=1;
            #        a=2;
            # And these two are not:
            #        a=1;
            #        b=1;
            if cookie_object == stored_cookie_obj:
                break
        else:
            i = info.info()
            i.setPluginName(self.getName())
            i.setName('Cookie')
            i.setURL( response.getURL() )
            
            self._set_cookie_to_rep(i, cstr=cookie_header_value)
            
            i['cookie-object'] = cookie_object

            '''
            The expiration date tells the browser when to delete the 
            cookie. If no expiration date is provided, the cookie is
            deleted at the end of the user session, that is, when the
            user quits the browser. As a result, specifying an expiration
            date is a means for making cookies to survive across
            browser sessions. For this reason, cookies that have an
            expiration date are called persistent.
            '''
            i['persistent'] = 'expires' in cookie_object
            i.set_id( response.id )
            i.addToHighlight(i['cookie-string'])
            msg = 'The URL: "%s" sent the cookie: "%s".' 
            i.setDesc( msg % (i.getURL(), i['cookie-string']) )
            kb.kb.append( self, 'cookies', i )

    
    def _parse_cookie(self, request, response, cookie_header_value):
        '''
        If the response sets more than one Cookie, this method will
        be called once for each "Set-Cookie" header.

        @param request: The HTTP request object.
        @param response: The HTTP response object
        @param cookie_header_value: The cookie, as sent in the HTTP response
        
        @return: The cookie object or None if the parsing failed
        '''         
        cookie_object = Cookie.SimpleCookie()
        try:
            # Note to self: This line may print some chars to the console
            cookie_object.load( cookie_header_value )
        except Cookie.CookieError:

            i = info.info()
            i.setPluginName(self.getName())
            i.setName('Invalid cookie')
            i.setURL( response.getURL() )
            
            self._set_cookie_to_rep(i, cstr=cookie_header_value)
            
            # The cookie is invalid, this is worth mentioning ;)
            msg = 'The remote Web application sent a cookie with an incorrect' \
                  ' format: "%s" that does NOT respect the RFC.' % cookie_header_value
            i.setDesc(msg)
            kb.kb.append( self, 'invalid-cookies', i )
            return None
        
        else:
            return cookie_object
        
    
    def _analyze_cookie_security( self, request, response, cookie_obj, 
                                  cookie_header_value ):
        '''
        In this method I call all the other methods that perform a specific
        analysis of the already catched cookie.
        '''
        self._secure_over_http( request, response, cookie_obj, cookie_header_value )
        self._not_secure_over_https( request, response, cookie_obj, cookie_header_value )
        
        fingerprinted = self._match_cookie_fingerprint( request, response, cookie_obj )
        self._http_only( request, response, cookie_obj, 
                         cookie_header_value, fingerprinted )
        
        
    def _http_only(self, request, response, cookie_obj, 
                   cookie_header_value, fingerprinted):
        '''
        Verify if the cookie has the httpOnly parameter set
        
        Reference:
            http://www.owasp.org/index.php/HTTPOnly
            http://en.wikipedia.org/wiki/HTTP_cookie
        
        @param request: The http request object
        @param response: The http response object
        @param cookie_obj: The cookie object to analyze
        @param cookie_header_value: The cookie, as sent in the HTTP response
        @param fingerprinted: True if the cookie was fingerprinted 
        @return: None
        '''
        if not self.HTTPONLY_RE.search(cookie_header_value):
            
            v = vuln.vuln()
            v.setPluginName(self.getName())
            v.setURL( response.getURL() )
            v.set_id( response.getId() )
            self._set_cookie_to_rep(v, cobj=cookie_obj)
            
            httponly_severity = severity.MEDIUM if fingerprinted else severity.LOW
            v.setSeverity(httponly_severity)
            
            v.setName( 'Cookie without HttpOnly' )
            msg = 'A cookie without the HttpOnly flag was sent when requesting' \
                  ' "%s". The HttpOnly flag prevents potential intruders from' \
                  ' accessing the cookie value through Cross-Site Scripting' \
                  ' attacks.'
            v.setDesc( msg % response.getURL() )
            kb.kb.append( self, 'security', v )
            
    def _ssl_cookie_via_http( self, request, response ):
        '''
        Analyze if a cookie value, sent in a HTTPS request, is now used for 
        identifying the user in an insecure page. Example:
            Login is done over SSL
            The rest of the page is HTTP
        '''
        if request.getURL().getProtocol().lower() == 'http':
            for cookie in kb.kb.get( 'analyze_cookies', 'cookies' ):
                if cookie.getURL().getProtocol().lower() == 'https' and \
                request.getURL().getDomain() == cookie.getURL().getDomain():
                    # The cookie was sent using SSL, I'll check if the current 
                    # request, is using this values in the POSTDATA / QS / COOKIE
                    for key in cookie['cookie-object'].keys():
                        # This if is to create less false positives
                        if len( cookie['cookie-object'][key] ) > 6 and \
                        cookie['cookie-object'][key] in request.dump():
                            v = vuln.vuln()
                            v.setPluginName(self.getName())
                            v.setURL( response.getURL() )
                            v.setSeverity(severity.HIGH)
                            v.set_id( response.id )
                            v.setName( 'Secure cookies over insecure channel' )
                            msg = 'Cookie values that were set over HTTPS, are' \
                                  ' then sent over an insecure channel in a request' \
                                  ' to "%s".' % request.getURL() 
                            v.setDesc( msg )
                            self._set_cookie_to_rep(v, cobj=cookie)
                            kb.kb.append( self, 'security', v )
            
    def _match_cookie_fingerprint( self, request, response, cookie_obj ):
        '''
        Now we analyze the cookie and try to guess the remote web server or 
        programming framework based on the cookie that was sent.
        
        @return: True if the cookie was fingerprinted
        '''
        cookie_obj_str = cookie_obj.output(header='')
        
        for cookie_str_db, system_name in self.COOKIE_FINGERPRINT:
            if cookie_str_db in cookie_obj_str:
                if system_name not in self._already_reported_server:
                    i = info.info()
                    i.setPluginName(self.getName())
                    i.set_id( response.id )
                    i.setName('Identified cookie')
                    i.setURL( response.getURL() )
                    self._set_cookie_to_rep(i, cobj=cookie_obj)
                    i['httpd'] = system_name
                    i.setDesc( 'A cookie matching the cookie fingerprint DB' \
                               ' has been found when requesting "%s".' \
                               ' The remote platform is: "%s".' \
                               % (response.getURL(), system_name) )
                    kb.kb.append( self, 'security', i )
                    self._already_reported_server.append( system_name )
                    return True
        
        return False

    def _secure_over_http( self, request, response, cookie_obj, cookie_header_value ):
        '''
        Checks if a cookie marked as secure is sent over http.
        
        Reference:
            http://en.wikipedia.org/wiki/HTTP_cookie
        
        @param request: The http request object
        @param response: The http response object
        @param cookie_obj: The cookie object to analyze
        @param cookie_header_value: The cookie, as sent in the HTTP response
        @return: None
        '''
        # BUGBUG: http://bugs.python.org/issue1028088
        #
        # I workaround this issue by using the raw string from the HTTP
        # response instead of the parsed:
        #
        #        cookie_obj_str = cookie_obj.output(header='')
        #
        # Bug can be reproduced like this:
        # >>> import Cookie
        # >>> cookie_object = Cookie.SimpleCookie()
        # >>> cookie_object.load('a=b; secure; httponly')
        # >>> cookie_object.output(header='')
        # ' a=b'
        #
        # Note the missing secure/httponly in the output return 
        
        # And now, the code:
        if self.SECURE_RE.search(cookie_header_value) and \
        response.getURL().getProtocol().lower() == 'http':
            v = vuln.vuln()
            v.setPluginName(self.getName())
            v.setURL( response.getURL() )
            v.set_id( response.getId() )
            self._set_cookie_to_rep(v, cobj=cookie_obj)
            v.setSeverity(severity.HIGH)
            v.setName( 'Secure cookie over HTTP' )
            msg = 'A cookie marked with the secure flag was sent over' \
                  ' an insecure channel (HTTP) when requesting the URL:'\
                  ' "%s", this usually means that the Web application was'\
                  ' designed to run over SSL and was deployed without security'\
                  ' or that the developer does not understand the "secure" flag.' 
            v.setDesc( msg % response.getURL() )
            kb.kb.append( self, 'security', v )
        
    def _not_secure_over_https( self, request, response, cookie_obj, 
                                cookie_header_value ):
        '''
        Checks if a cookie that does NOT have a secure flag is sent over https.
        
        @param request: The http request object
        @param response: The http response object
        @param cookie_obj: The cookie object to analyze
        @param cookie_header_value: The cookie, as sent in the HTTP response
        @return: None
        '''
        # BUGBUG: See other reference in this file for http://bugs.python.org/issue1028088
        
        if response.getURL().getProtocol().lower() == 'https' and \
        not self.SECURE_RE.search(cookie_header_value):
            v = vuln.vuln()
            v.setPluginName(self.getName())
            v.setURL( response.getURL() )
            v.set_id( response.getId() )
            self._set_cookie_to_rep(v, cobj=cookie_obj)
            v.setSeverity(severity.HIGH)
            v.setName( 'Secure flag missing in HTTPS cookie' )
            msg = 'A cookie without the secure flag was sent in an HTTPS' \
                  ' response at "%s". The secure flag prevents the browser' \
                  ' from sending a "secure" cookie over an insecure HTTP' \
                  ' channel, thus preventing potential session hijacking' \
                  ' attacks.'
            v.setDesc( msg % response.getURL() )
            kb.kb.append( self, 'security', v )

    def end(self):
        '''
        This method is called when the plugin wont be used anymore.
        '''
        cookies = kb.kb.get( 'analyze_cookies', 'cookies' )
            
        tmp = list(set([(c['cookie-string'], c.getURL() ) for c in cookies]))
        res_dict, item_idx = group_by_min_key( tmp )
        if not item_idx:
            # Grouped by URLs
            msg = 'The URL: "%s" sent these cookies:'
        else:
            # Grouped by cookies
            msg = 'The cookie: "%s" was sent by these URLs:'
            
        for k in res_dict:
            to_print = msg % k
            
            for i in res_dict[k]:
                to_print += '\n- ' + i
            
            om.out.information(to_print)

    def _set_cookie_to_rep(self, info_inst, cobj=None, cstr=None):
        if cobj is not None:
            info_inst['cookie-object'] = cobj
            cstr = cobj.output(header='')
        
        if cstr is not None:
            info_inst['cookie-string'] = cstr
            info_inst.addToHighlight(cstr)
    
    def get_long_desc( self ):
        '''
        @return: A DETAILED description of the plugin functions and features.
        '''
        return '''
        This plugin greps every response for session cookies that the web 
        application sends to the client, and analyzes them in order to identify
        potential vulnerabilities, the remote web application framework and
        other interesting information.
        '''