"""Can GitHub reach CeX's price data (and eBay's public pages)?"""
import json, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://uk.webuy.com", "Referer": "https://uk.webuy.com/"}
tests = {
    "cex_search": "https://wss2.cex.uk.webuy.io/v3/boxes?q=iphone%2013&firstRecord=1&count=5",
    "cex_search_alt": "https://wss2.cex.uk.webuy.io/v3/boxes?q=ps5&firstRecord=1&count=5&sortBy=relevance&sortOrder=desc",
    "cex_box": "https://wss2.cex.uk.webuy.io/v3/boxes/711719541028",
    "ebay_api_host": "https://api.ebay.com/buy/browse/v1/item_summary/search?q=ps5",
}
for name, url in tests.items():
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
            body = r.read().decode(errors="replace")
        print(name, r.status, body[:500].replace("\n", " "))
    except urllib.error.HTTPError as e:
        print(name, "HTTP", e.code, e.read().decode(errors="replace")[:200].replace("\n", " "))
    except Exception as e:
        print(name, "ERR", e)
