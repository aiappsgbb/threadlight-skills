// =============================================================================
// TEST STAND-IN for a customer's own (non-Azure) model gateway. NOT generated.
//
// A plain nginx pass-through on the pilot's internal Container Apps environment,
// standing in for a gateway the customer runs elsewhere (Kong, Apigee, an
// OpenAI-compatible proxy on another cloud). It proves the contract of
// `--model-gateway external`: the agent sends its Responses calls to
// THREADLIGHT_MODEL_GATEWAY_URL (ending in /openai/v1) and the gateway forwards
// them, streaming, with the caller's Microsoft Entra token, to the project's
// /openai/v1 endpoint. It adds an `x-threadlight-gateway: standin` header and
// logs every request to stdout. No image build: the MCR nginx image plus an
// inline config.
// =============================================================================
param location string = resourceGroup().location
param name string
param environmentId string
param projectEndpoint string
param tags object = {}

var upstreamHost = split(replace(projectEndpoint, 'https://', ''), '/')[0]
var conf = '''
events {}
http {
  log_format gw '$time_iso8601 $request_method $request_uri -> $status $upstream_status $request_time ua="$http_user_agent" len=$content_length err=$upstream_http_x_ms_error_code wwwauth="$upstream_http_www_authenticate" reqid=$upstream_http_apim_request_id';
  access_log /dev/stdout gw;
  error_log /dev/stderr warn;
  server {
    listen 8080;
    location /openai/v1/ {
      proxy_pass __UPSTREAM__/openai/v1/;
      proxy_ssl_server_name on;
      proxy_set_header Host __HOST__;
      proxy_http_version 1.1;
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_read_timeout 300s;
      add_header x-threadlight-gateway standin always;
    }
    location = /healthz { return 200 'ok'; }
  }
}
'''

resource gateway 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: { ingress: { external: true, targetPort: 8080, transport: 'auto' } }
    template: {
      containers: [ {
        name: 'nginx'
        image: 'mcr.microsoft.com/azurelinux/base/nginx:1.25'
        command: [ '/bin/sh', '-c' ]
        args: [ 'printf "%s" "$NGINX_CONF" > /etc/nginx/nginx.conf && exec nginx -g "daemon off;"' ]
        env: [ { name: 'NGINX_CONF', value: replace(replace(conf, '__UPSTREAM__', projectEndpoint), '__HOST__', upstreamHost) } ]
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
      } ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output gatewayUrl string = 'https://${gateway.properties.configuration.ingress.fqdn}/openai/v1'
