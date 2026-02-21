import React, { useState } from 'react';
import './App.css';

function App() {
  const [zipCode, setZipCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [homeData, setHomeData] = useState(null);
  const [marketTrends, setMarketTrends] = useState(null);

  const formatCurrency = (value) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    });
  };

  const getTrendIcon = (trend) => {
    switch (trend) {
      case 'up':
        return '📈';
      case 'down':
        return '📉';
      default:
        return '➡️';
    }
  };

  const getTrendColor = (trend) => {
    switch (trend) {
      case 'up':
        return '#10b981';
      case 'down':
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  const getChangeColor = (change) => {
    return change >= 0 ? '#10b981' : '#ef4444';
  };

  const fetchHomeValue = async (e) => {
    e.preventDefault();

    if (!zipCode || zipCode.length !== 5) {
      setError('Please enter a valid 5-digit zip code');
      return;
    }

    setLoading(true);
    setError(null);
    setHomeData(null);
    setMarketTrends(null);

    try {
      // Fetch home value data
      const homeResponse = await fetch(`/api/home-value/${zipCode}`);
      const homeResult = await homeResponse.json();

      if (!homeResult.success) {
        throw new Error(homeResult.error || 'Failed to fetch home value data');
      }

      setHomeData(homeResult.data);

      // Fetch market trends
      const trendsResponse = await fetch(`/api/market-trends/${zipCode}`);
      const trendsResult = await trendsResponse.json();

      if (trendsResult.success) {
        setMarketTrends(trendsResult.data);
      }
    } catch (err) {
      setError(err.message || 'An error occurred while fetching data');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>🏠 Home Value Lookup</h1>
        <p className="subtitle">Find average home values based on recent sales in your area</p>
      </header>

      <form onSubmit={fetchHomeValue} className="search-form">
        <input
          type="text"
          value={zipCode}
          onChange={(e) => setZipCode(e.target.value.replace(/\D/g, '').slice(0, 5))}
          placeholder="Enter zip code (e.g., 90210)"
          className="zip-input"
          maxLength={5}
          pattern="\d{5}"
        />
        <button type="submit" disabled={loading || zipCode.length !== 5} className="search-button">
          {loading ? 'Searching...' : 'Search'}
        </button>
      </form>

      {error && (
        <div className="error-message">
          ⚠️ {error}
        </div>
      )}

      {homeData && (
        <div className="results">
          <div className="location-header">
            <h2>{homeData.city}, {homeData.state} {homeData.zip}</h2>
            {homeData.note && <p className="note">{homeData.note}</p>}
          </div>

          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-label">Average Home Value</div>
              <div className="stat-value">{formatCurrency(homeData.averageHomeValue)}</div>
            </div>

            <div className="stat-card">
              <div className="stat-label">Median Home Value</div>
              <div className="stat-value">{formatCurrency(homeData.medianHomeValue)}</div>
            </div>

            <div className="stat-card">
              <div className="stat-label">Price per Sq Ft</div>
              <div className="stat-value">{formatCurrency(homeData.averagePricePerSqFt)}</div>
            </div>

            <div className="stat-card">
              <div className="stat-label">Market Trend</div>
              <div className="stat-value" style={{ color: getTrendColor(homeData.marketTrend) }}>
                {getTrendIcon(homeData.marketTrend)} {homeData.marketTrend.charAt(0).toUpperCase() + homeData.marketTrend.slice(1)}
              </div>
            </div>

            <div className="stat-card">
              <div className="stat-label">Year-over-Year Change</div>
              <div className="stat-value" style={{ color: getChangeColor(homeData.yearOverYearChange) }}>
                {homeData.yearOverYearChange >= 0 ? '+' : ''}{homeData.yearOverYearChange}%
              </div>
            </div>

            <div className="stat-card">
              <div className="stat-label">Last Updated</div>
              <div className="stat-value small">{formatDate(homeData.lastUpdated)}</div>
            </div>
          </div>

          <div className="section">
            <h3>Recent Sales</h3>
            <div className="sales-list">
              {homeData.recentSales.map((sale, index) => (
                <div key={index} className="sale-item">
                  <div className="sale-info">
                    <div className="sale-price">{formatCurrency(sale.price)}</div>
                    <div className="sale-address">{sale.address}</div>
                  </div>
                  <div className="sale-details">
                    <span>{sale.sqft.toLocaleString()} sq ft</span>
                    <span className="sale-date">{formatDate(sale.date)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {marketTrends && (
            <div className="section">
              <h3>6-Month Market Trend</h3>
              <div className="trend-chart">
                {marketTrends.sixMonthTrend.map((point, index) => {
                  const maxVal = Math.max(...marketTrends.sixMonthTrend.map(p => p.value));
                  const minVal = Math.min(...marketTrends.sixMonthTrend.map(p => p.value));
                  const range = maxVal - minVal || 1;
                  const height = ((point.value - minVal) / range) * 80 + 20;

                  return (
                    <div key={index} className="trend-bar-container">
                      <div className="trend-bar" style={{ height: `${height}%` }}></div>
                      <div className="trend-label">{point.month.split(' ')[0]}</div>
                      <div className="trend-value">{formatCurrency(point.value)}</div>
                    </div>
                  );
                })}
              </div>

              <div className="market-stats">
                <div className="market-stat">
                  <span className="market-stat-label">Days on Market:</span>
                  <span className="market-stat-value">{marketTrends.averageDaysOnMarket} days</span>
                </div>
                <div className="market-stat">
                  <span className="market-stat-label">Months of Supply:</span>
                  <span className="market-stat-value">{marketTrends.monthsOfSupply} months</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <footer className="footer">
        <p>💡 Try these popular zip codes: 90210 (Beverly Hills) • 10001 (NYC) • 33101 (Miami) • 77001 (Houston) • 60601 (Chicago)</p>
        <p className="disclaimer">Data provided for demonstration purposes. Connect to a real estate API for production use.</p>
      </footer>
    </div>
  );
}

export default App;
