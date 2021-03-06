Wind: Super-fast web framework
==============================
Wind is microframework based on asynchronous, non-blocking server benchmarking `tornado`.

Usage sketch.

        from wind.web.httpserver import HTTPServer
        from wind.web.app import WindApp, path, Resource

        def hello_wind(request):
            return 'hello wind!'
        
        class HelloResource(Resource):
            def handle_get(self):
                self.write('hello wind!')
                self.finish()
                
        app = WindApp([
                path(hello_wind, route='/', methods=['get']),
                path(HelloResource, route='/resource', methods=['get'])
                ])
        server = HTTPServer(app=app)
        server.run_simple('127.0.0.1', 9000)

This usage interfaces are made for the purpose of testing performance.
