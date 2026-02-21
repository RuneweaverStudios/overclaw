# Home Value Lookup App

A Node.js and React application that fetches average home values in a zip code based on recent sales data.

## Features

- **Home Value Search**: Look up average and median home values by zip code
- **Recent Sales Data**: View recent property sales in the area with prices and details
- **Market Trends**: Visual 6-month market trend charts
- **Market Statistics**: Days on market and months of supply data
- **Responsive Design**: Works on desktop and mobile devices

## Architecture

### Backend (Node.js/Express)
- RESTful API with endpoints for home value data and market trends
- CORS enabled for cross-origin requests from frontend
- Mock data for demonstration (can be replaced with real API calls)

### Frontend (React/Vite)
- Modern React with hooks (useState)
- Vite for fast development and optimized builds
- Proxy configuration for seamless API communication
- Responsive CSS with gradient design

## Installation

### Prerequisites
- Node.js 18+ and npm

### Backend Setup

```bash
cd home-value-app/backend
npm install
npm start
```

The backend will run on `http://localhost:3001`

### Frontend Setup

```bash
cd home-value-app/frontend
npm install
npm run dev
```

The frontend will run on `http://localhost:3000`

## API Endpoints

### `GET /api/home-value/:zip`
Fetch home value data for a specific zip code.

**Response:**
```json
{
  "success": true,
  "data": {
    "zip": "90210",
    "city": "Beverly Hills",
    "state": "CA",
    "averageHomeValue": 2850000,
    "medianHomeValue": 2200000,
    "averagePricePerSqFt": 1150,
    "recentSales": [...],
    "marketTrend": "up",
    "yearOverYearChange": 8.5
  }
}
```

### `GET /api/market-trends/:zip`
Fetch 6-month market trend data for a zip code.

**Response:**
```json
{
  "success": true,
  "data": {
    "zip": "90210",
    "sixMonthTrend": [
      { "month": "Aug 2024", "value": 100000 },
      ...
    ],
    "averageDaysOnMarket": 25,
    "monthsOfSupply": 2
  }
}
```

### `GET /api/health`
Health check endpoint.

## Data Sources

This app currently uses **mock data** for demonstration purposes. To integrate with real estate APIs:

### Paid/Authenticated Options
- **Zillow API**: Requires API key registration
- **Realtor.com API**: Requires partnership
- **Redfin Data Center**: Enterprise access
- **ATTOM Data Solutions**: Commercial API
- **Estated**: Paid service

### Free/Public Options
- County assessor public records (varies by county)
- Federal Housing Finance Agency (FHFA) data
- Census Bureau housing data
- Web scraping with proper rate limiting and terms of service compliance

To replace mock data with real API calls, modify the `server.js` file:

```javascript
// Example: Replace mock data with real API call
app.get('/api/home-value/:zip', async (req, res) => {
  const { zip } = req.params;
  try {
    const response = await fetch(`https://api.example.com/homes/${zip}`, {
      headers: { 'Authorization': `Bearer ${process.env.API_KEY}` }
    });
    const data = await response.json();
    res.json({ success: true, data });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});
```

## Sample Zip Codes

The app includes sample data for these zip codes:
- **90210** - Beverly Hills, CA
- **10001** - New York, NY
- **33101** - Miami, FL
- **77001** - Houston, TX
- **60601** - Chicago, IL
- **98101** - Seattle, WA
- **02101** - Boston, MA
- **80201** - Denver, CO

For other zip codes, the app generates simulated data to demonstrate functionality.

## Development

### Backend Development
```bash
cd backend
npm run dev  # Runs with --watch flag for auto-restart
```

### Frontend Development
```bash
cd frontend
npm run dev  # Starts Vite dev server with hot reload
```

### Building for Production

Frontend:
```bash
cd frontend
npm run build
```

The build output will be in `frontend/dist/`

## Technologies Used

### Backend
- **Express**: Fast, minimalist web framework
- **CORS**: Cross-origin resource sharing middleware
- **Node.js**: JavaScript runtime

### Frontend
- **React**: UI library
- **Vite**: Next-generation frontend tooling
- **CSS3**: Modern styling with gradients and transitions

## License

MIT

## Contributing

Contributions welcome! Please feel free to submit issues or pull requests.
