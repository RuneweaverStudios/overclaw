import express from 'express';
import cors from 'cors';

const app = express();
const PORT = process.env.PORT || 3001;

// Middleware
app.use(cors());
app.use(express.json());

// Mock home value data by zip code
// This can be replaced with real API calls (Zillow, Redfin, Realtor.com, etc.)
const mockHomeData = {
  '90210': {
    zip: '90210',
    city: 'Beverly Hills',
    state: 'CA',
    averageHomeValue: 2850000,
    medianHomeValue: 2200000,
    averagePricePerSqFt: 1150,
    recentSales: [
      { price: 3100000, sqft: 2800, address: '123 Palm Dr', date: '2025-01-15' },
      { price: 2750000, sqft: 2400, address: '456 Maple Ave', date: '2025-01-10' },
      { price: 2900000, sqft: 2600, address: '789 Oak St', date: '2025-01-05' }
    ],
    marketTrend: 'up',
    yearOverYearChange: 8.5
  },
  '10001': {
    zip: '10001',
    city: 'New York',
    state: 'NY',
    averageHomeValue: 1200000,
    medianHomeValue: 950000,
    averagePricePerSqFt: 1450,
    recentSales: [
      { price: 1350000, sqft: 900, address: '123 5th Ave', date: '2025-01-14' },
      { price: 1100000, sqft: 750, address: '456 Madison Ave', date: '2025-01-09' },
      { price: 1250000, sqft: 850, address: '789 Park Ave', date: '2025-01-03' }
    ],
    marketTrend: 'stable',
    yearOverYearChange: 2.3
  },
  '33101': {
    zip: '33101',
    city: 'Miami',
    state: 'FL',
    averageHomeValue: 450000,
    medianHomeValue: 380000,
    averagePricePerSqFt: 380,
    recentSales: [
      { price: 480000, sqft: 1200, address: '123 Ocean Dr', date: '2025-01-16' },
      { price: 420000, sqft: 1100, address: '456 Collins Ave', date: '2025-01-11' },
      { price: 450000, sqft: 1180, address: '789 Washington Ave', date: '2025-01-07' }
    ],
    marketTrend: 'up',
    yearOverYearChange: 6.8
  },
  '77001': {
    zip: '77001',
    city: 'Houston',
    state: 'TX',
    averageHomeValue: 320000,
    medianHomeValue: 275000,
    averagePricePerSqFt: 185,
    recentSales: [
      { price: 350000, sqft: 1800, address: '123 Main St', date: '2025-01-13' },
      { price: 290000, sqft: 1600, address: '456 Texas Ave', date: '2025-01-08' },
      { price: 320000, sqft: 1750, address: '789 Travis St', date: '2025-01-04' }
    ],
    marketTrend: 'down',
    yearOverYearChange: -1.2
  },
  '60601': {
    zip: '60601',
    city: 'Chicago',
    state: 'IL',
    averageHomeValue: 380000,
    medianHomeValue: 320000,
    averagePricePerSqFt: 295,
    recentSales: [
      { price: 420000, sqft: 1400, address: '123 Michigan Ave', date: '2025-01-12' },
      { price: 350000, sqft: 1200, address: '456 Wacker Dr', date: '2025-01-06' },
      { price: 380000, sqft: 1300, address: '789 Lasalle St', date: '2025-01-02' }
    ],
    marketTrend: 'stable',
    yearOverYearChange: 1.5
  },
  '98101': {
    zip: '98101',
    city: 'Seattle',
    state: 'WA',
    averageHomeValue: 720000,
    medianHomeValue: 620000,
    averagePricePerSqFt: 620,
    recentSales: [
      { price: 780000, sqft: 1200, address: '123 Pine St', date: '2025-01-14' },
      { price: 680000, sqft: 1100, address: '456 4th Ave', date: '2025-01-09' },
      { price: 720000, sqft: 1150, address: '789 Union St', date: '2025-01-05' }
    ],
    marketTrend: 'up',
    yearOverYearChange: 5.2
  },
  '02101': {
    zip: '02101',
    city: 'Boston',
    state: 'MA',
    averageHomeValue: 680000,
    medianHomeValue: 580000,
    averagePricePerSqFt: 720,
    recentSales: [
      { price: 750000, sqft: 1000, address: '123 State St', date: '2025-01-15' },
      { price: 620000, sqft: 850, address: '456 Washington St', date: '2025-01-10' },
      { price: 680000, sqft: 950, address: '789 Tremont St', date: '2025-01-06' }
    ],
    marketTrend: 'up',
    yearOverYearChange: 7.1
  },
  '80201': {
    zip: '80201',
    city: 'Denver',
    state: 'CO',
    averageHomeValue: 485000,
    medianHomeValue: 420000,
    averagePricePerSqFt: 380,
    recentSales: [
      { price: 520000, sqft: 1350, address: '123 16th St', date: '2025-01-13' },
      { price: 450000, sqft: 1200, address: '456 Larimer St', date: '2025-01-08' },
      { price: 485000, sqft: 1280, address: '789 Blake St', date: '2025-01-04' }
    ],
    marketTrend: 'stable',
    yearOverYearChange: 3.4
  }
};

/**
 * Get home value data for a specific zip code
 * Returns average home value based on recent sales in the area
 *
 * In production, this would integrate with APIs like:
 * - Zillow API (requires API key)
 * - Redfin Data Center (requires partnership)
 * - Realtor.com API (requires API key)
 * - ATTOM Data Solutions (paid API)
 * - Estated API (paid)
 *
 * Free alternatives:
 * - Web scraping with proper rate limiting
 * - County assessor public records APIs
 * - Census Bureau data for broader statistics
 */
app.get('/api/home-value/:zip', (req, res) => {
  const { zip } = req.params;

  // Validate zip code format (5 digits)
  const zipRegex = /^\d{5}$/;
  if (!zipRegex.test(zip)) {
    return res.status(400).json({
      error: 'Invalid zip code format. Please provide a 5-digit US zip code.'
    });
  }

  // Check if we have data for this zip
  const data = mockHomeData[zip];

  if (data) {
    // Return cached data
    return res.json({
      success: true,
      data: {
        ...data,
        lastUpdated: new Date().toISOString()
      }
    });
  }

  // For zip codes not in our mock database, return a generic response
  // In production, this would query the actual API
  const hash = zip.split('').reduce((a, b) => a + b.charCodeAt(0), 0);
  const baseValue = 200000 + (hash % 500000);

  return res.json({
    success: true,
    data: {
      zip,
      city: 'Unknown',
      state: 'US',
      averageHomeValue: baseValue,
      medianHomeValue: baseValue * 0.85,
      averagePricePerSqFt: 200 + (hash % 400),
      recentSales: [
        {
          price: baseValue,
          sqft: 1500 + (hash % 1000),
          address: `${100 + (hash % 9999)} Main St`,
          date: new Date(Date.now() - Math.random() * 15 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
        }
      ],
      marketTrend: ['up', 'down', 'stable'][hash % 3],
      yearOverYearChange: (Math.random() * 20 - 5).toFixed(1),
      lastUpdated: new Date().toISOString(),
      note: 'This is simulated data. Connect to a real estate API for accurate data.'
    }
  });
});

/**
 * Get market trends for a zip code
 */
app.get('/api/market-trends/:zip', (req, res) => {
  const { zip } = req.params;

  // Validate zip code format
  const zipRegex = /^\d{5}$/;
  if (!zipRegex.test(zip)) {
    return res.status(400).json({
      error: 'Invalid zip code format.'
    });
  }

  const hash = zip.split('').reduce((a, b) => a + b.charCodeAt(0), 0);

  // Generate mock trend data
  res.json({
    success: true,
    data: {
      zip,
      sixMonthTrend: [
        { month: 'Aug 2024', value: 100000 + hash % 300000 },
        { month: 'Sep 2024', value: 105000 + hash % 315000 },
        { month: 'Oct 2024', value: 102000 + hash % 306000 },
        { month: 'Nov 2024', value: 108000 + hash % 324000 },
        { month: 'Dec 2024', value: 110000 + hash % 330000 },
        { month: 'Jan 2025', value: 115000 + hash % 345000 }
      ],
      averageDaysOnMarket: 25 + (hash % 50),
      monthsOfSupply: 2 + (hash % 4),
      note: 'This is simulated data.'
    }
  });
});

/**
 * Health check endpoint
 */
app.get('/api/health', (req, res) => {
  res.json({
    success: true,
    message: 'Home Value API is running',
    timestamp: new Date().toISOString()
  });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({
    success: false,
    error: 'Endpoint not found'
  });
});

// Error handler
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({
    success: false,
    error: 'Internal server error'
  });
});

// Start server
app.listen(PORT, () => {
  console.log(`Home Value API server running on port ${PORT}`);
  console.log(`API endpoints:`);
  console.log(`  GET /api/home-value/:zip`);
  console.log(`  GET /api/market-trends/:zip`);
  console.log(`  GET /api/health`);
});
