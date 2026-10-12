"""Deploy private Pages output with Wrangler and Composio-managed API access."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

from snapshots import ROOT, STATE

API = 'https://api.cloudflare.com/client/v4'


def api(path, method='GET', body=None, content_type='application/json', allow_failure=False):
    multipart = content_type.startswith('multipart/form-data')
    command = ['composio', 'proxy', API + path, '--toolkit', 'cloudflare_api_key', '--method', method]
    with tempfile.TemporaryDirectory(prefix='warm-up-cloudflare-') as tmp:
        if multipart:
            options = {'method': method, 'path': path, 'contentType': content_type, 'rawBody': True,
                       'body': body.decode('utf-8')}
            payload = Path(tmp) / 'mcp.json'
            payload.write_text(json.dumps({'account_id': path.split('/')[2],
                'code': 'async () => await cloudflare.request(' + json.dumps(options) + ')'}))
            payload.chmod(0o600)
            command = ['composio', 'execute', 'CLOUDFLARE_MCP_EXECUTE', '-d', '@' + str(payload)]
        elif body is not None:
            payload = Path(tmp) / 'payload'
            payload.write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())
            payload.chmod(0o600)
            command += ['--header', 'Content-Type: ' + content_type, '--data', '@' + str(payload)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Cloudflare request failed: ' + method + ' ' + path)
    response = json.loads(result.stdout)
    if response.get('storedInFile'):
        spilled = Path(response['outputFilePath'])
        spilled.chmod(0o600)
        response = json.loads(spilled.read_text())
        spilled.unlink()
    if multipart:
        if not response.get('successful'):
            raise RuntimeError('Cloudflare MCP refused the multipart deployment: ' + str(response.get('error', ''))[:600])
        response = response['data']
    if not response.get('success') and not allow_failure:
        errors = [str(item.get('code')) + ': ' + str(item.get('message', ''))[:400]
                  for item in response.get('errors', [])]
        raise RuntimeError('Cloudflare refused ' + method + ' ' + path + ': ' + ','.join(errors))
    return response


def fingerprint(config):
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    files = list((STATE / 'docs').rglob('*')) + list((ROOT / 'functions').rglob('*.js'))
    files += [ROOT / 'package-lock.json']
    for path in sorted(item for item in files if item.is_file()):
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def deploy():
    os.umask(0o077)
    config = json.loads((STATE / 'cloudflare.json').read_text())
    token = api('/accounts/' + config['account_id'] + '/tokens/verify')['result']
    if token.get('status') != 'active':
        raise RuntimeError('The managed Cloudflare account API token is not active.')
    project_path = '/accounts/' + config['account_id'] + '/pages/projects/' + config['project']
    project = api(project_path)['result']
    for environment in ['production', 'preview']:
        settings = project['deployment_configs'][environment]
        env = settings.get('env_vars') or {}
        if (settings.get('fail_open') is not False or
                env.get('CF_ACCESS_DOMAIN', {}).get('value') != config['access_domain'] or
                env.get('CF_ACCESS_AUD', {}).get('value') != config['access_aud']):
            raise RuntimeError('Pages Access settings do not match the private deployment configuration.')
    app = api('/accounts/' + config['account_id'] + '/access/apps/' + config['access_app_id'])['result']
    hosts = {item.get('uri') for item in app['destinations']}
    if (hosts != {config['project'] + '.pages.dev', '*.' + config['project'] + '.pages.dev'} or
            app['allowed_idps'] != [config['google_idp_id']] or app['aud'] != config['access_aud']):
        raise RuntimeError('Access application configuration changed; no deployment made.')
    policies = app.get('policies', [])
    if (len(policies) != 1 or policies[0].get('id') != config['policy_id'] or
            policies[0].get('decision') != 'allow' or
            policies[0].get('include') != [{'email_domain': {'domain': 'ara-research.ai'}}] or
            policies[0].get('require') != [{'login_method': {'id': config['google_idp_id']}}]):
        raise RuntimeError('Access policy changed; no deployment made.')
    routes = json.loads((STATE / 'docs' / '_routes.json').read_text())
    if routes.get('include') != ['/*'] or routes.get('exclude') != []:
        raise RuntimeError('Every Pages path must run the authentication middleware.')
    version = fingerprint(config)
    receipt_path = STATE / 'deployment.json'
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        canonical = project.get('canonical_deployment') or {}
        if previous.get('fingerprint') == version and canonical.get('id') == previous.get('id'):
            return previous

    # Wrangler retains its official asset hashing, multipart packaging and upload logic.
    # Only account API requests pass through Composio; asset requests use Pages' short-lived JWT.
    bridge_token = secrets.token_urlsafe(32)
    deployed = []
    failures = []

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def request(self):
            try:
                prefix = '/client/v4'
                if not self.path.startswith(prefix + '/'):
                    raise RuntimeError('Unexpected deployment request path.')
                path = self.path[len(prefix):]
                data = self.rfile.read(int(self.headers.get('Content-Length', '0'))) or None
                authorization = self.headers.get('Authorization', '')
                if path.startswith('/pages/assets/'):
                    if not authorization.startswith('Bearer ') or authorization == 'Bearer ' + bridge_token:
                        raise RuntimeError('Missing Pages asset upload authorization.')
                    request = urllib.request.Request(API + path, data=data, method=self.command,
                        headers={'Authorization': authorization,
                                 'Content-Type': self.headers.get('Content-Type', 'application/json')})
                    with urllib.request.urlopen(request, timeout=120) as response:
                        status, raw = response.status, response.read()
                else:
                    diagnostics = self.command == 'GET' and (
                        path in {'/user', '/user/tokens/verify', '/accounts', '/memberships'} or
                        path.startswith(('/accounts?', '/memberships?')))
                    if (authorization != 'Bearer ' + bridge_token or
                            not (path == project_path or path.startswith(project_path + '/') or
                                 diagnostics)):
                        raise RuntimeError('Request falls outside the authorized Pages project: ' + self.command + ' ' + path +
                                           ' (bridge authorization matched: ' + str(authorization == 'Bearer ' + bridge_token) + ')')
                    result = api(path, self.command, data, self.headers.get('Content-Type', 'application/json'),
                                 allow_failure=diagnostics)
                    status, raw = 200, json.dumps(result).encode()
                    if self.command == 'POST' and path == project_path + '/deployments':
                        deployed.append(result['result'])
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as error:
                failures.append(str(error) if isinstance(error, RuntimeError) else type(error).__name__)
                raw = json.dumps({'success': False, 'errors': [{'code': 10000, 'message': failures[-1]}]}).encode()
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        do_GET = request
        do_POST = request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = os.environ.copy()
        for key in list(environment):
            if key.startswith('CLOUDFLARE_'):
                environment.pop(key)
        environment.update({'CLOUDFLARE_API_BASE_URL': 'http://127.0.0.1:' + str(server.server_port) + '/client/v4',
                            'CLOUDFLARE_API_TOKEN': bridge_token, 'CLOUDFLARE_ACCOUNT_ID': config['account_id'],
                            'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
        command = [str(ROOT / 'node_modules' / '.bin' / 'wrangler'), 'pages', 'deploy', str(STATE / 'docs'),
                   '--project-name', config['project'], '--branch', config['branch'], '--commit-dirty=true']
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=600)
        if result.returncode or not deployed:
            log = STATE / 'wrangler-deploy.log'
            log.write_text(result.stdout + '\n' + result.stderr)
            log.chmod(0o600)
            # Wrangler output can include authentication details; return only the bounded diagnosis.
            raise RuntimeError('Pages deployment failed: ' + (failures[-1] if failures else 'Wrangler did not complete'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    current = api(project_path)['result']['canonical_deployment']
    if current['id'] != deployed[-1]['id'] or current['latest_stage']['status'] != 'success':
        raise RuntimeError('Cloudflare has not confirmed the exact production deployment.')
    receipt = {'id': current['id'], 'url': config['url'], 'deployment_url': current['url'],
               'created_on': current['created_on'], 'fingerprint': version}
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    receipt_path.chmod(0o600)
    return receipt


if __name__ == '__main__':
    try:
        print(json.dumps(deploy()))
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        raise SystemExit(1)
