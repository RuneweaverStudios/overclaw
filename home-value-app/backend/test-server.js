/**
 * Simple test script to verify API endpoints
 * Run with: node test-server.js
 */

import http from 'http';

const BASE_URL = 'http://localhost:3001';

function testEndpoint(endpoint, name) {
  return new Promise((resolve, reject) => {
    http.get(`${BASE_URL}${endpoint}`, (res) => {
      let data = '';

      res.on('data', (chunk) => {
        data += chunk;
      });

      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          if (json.success) {
            console.log(`✅ ${name}: PASSED`);
            resolve(true);
          } else {
            console.log(`❌ ${name}: FAILED - ${json.error || 'Unknown error'}`);
            resolve(false);
          }
        } catch (e) {
          console.log(`❌ ${name}: FAILED - Invalid JSON response`);
          resolve(false);
        }
      });
    }).on('error', (err) => {
      console.log(`❌ ${name}: FAILED - ${err.message}`);
      reject(err);
    });
  });
}

async function runTests() {
  console.log('🧪 Running Home Value API Tests...\n');

  const tests = [
    { endpoint: '/api/health', name: 'Health Check' },
    { endpoint: '/api/home-value/90210', name: 'Home Value - Beverly Hills (90210)' },
    { endpoint: '/api/home-value/10001', name: 'Home Value - NYC (10001)' },
    { endpoint: '/api/home-value/99999', name: 'Home Value - Unknown Zip (99999)' },
    { endpoint: '/api/market-trends/90210', name: 'Market Trends - Beverly Hills' },
    { endpoint: '/api/home-value/abc', name: 'Invalid Zip Code Format' }
  ];

  let passed = 0;
  let failed = 0;

  for (const test of tests) {
    try {
      const result = await testEndpoint(test.endpoint, test.name);
      if (result) passed++;
      else failed++;
    } catch (e) {
      failed++;
    }
  }

  console.log(`\n📊 Test Results: ${passed} passed, ${failed} failed`);

  if (failed === 0) {
    console.log('🎉 All tests passed!');
  } else {
    console.log('⚠️  Some tests failed. Make sure the server is running on port 3001');
  }
}

runTests().catch(console.error);
