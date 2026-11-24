# DailyScraper
A web scraping framework for any website: scrape with a real browser (Playwright) or with plain HTTP requests (planned) through one shared interface.

# Why DailyScraper

### Quick start, even for complex websites
- Covers everyday web scraping needs: 99% of scraped data follows the same `list → details` structure
- Get started without writing code: just copy the **XPath/CSS** selectors of the elements you need from your browser
- Every scraper is a simple, readable JSON file
- Export data to **CSV, XLSX, XML and JSON**
- Scraping status notifications by email and messengers
- Works with any catalog type: classic pagination or infinite scroll

### Customization
- Process data at every stage with your own Python handlers
- Add your own page templates and handlers for them
- Flexible detection of the catalog end, blocks and other scenarios
- Simple, clear architecture that is easy to extend
- Works with both sync and async code (planned)
- Handy CLI to set up your own project in minutes

### Reliability
- Anti-captcha service support (planned)
- Use proxies for both browser and HTTP scraping (HTTP is planned)
- Residential proxy support
- Automatic recovery of browser processes after errors
- Automatic proxy rotation and cooldown
- Page templates and fallback selectors
