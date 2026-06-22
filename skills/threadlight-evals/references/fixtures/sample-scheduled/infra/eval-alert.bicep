resource evalThresholdAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'eval-pass-rate-threshold-breach'
  location: 'global'
  properties: {
    description: 'Alert when continuous eval pass_rate falls below threshold'
    enabled: true
  }
}
