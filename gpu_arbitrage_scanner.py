import os
import requests
import json
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv

load_dotenv()

# Baseline Cloud Benchmark Rates ($/hr) — Lambda Labs / AWS / CoreWeave
BENCHMARK_RATES = {
    "RTX 4090": 0.74,   # Target market baseline $/hr
    "A100 80GB": 1.89,  # Target market baseline $/hr
    "H100": 3.49        # Target market baseline $/hr
}

VAST_API_KEY = os.getenv("VAST_AI_API_KEY", "")
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")

def fetch_vast_ai_offers():
    """Queries Vast.ai API for available GPU instances matching target models."""
    print("Fetching live market rates from Vast.ai...", flush=True)
    url = "https://console.vast.ai/api/v0/bundles"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if VAST_API_KEY:
        headers["Authorization"] = f"Bearer {VAST_API_KEY}"
    
    # Validated Vast.ai search query payload
    payload = {
        "limit": 100,
        "type": "on-demand",
        "reliability": {"gte": 0.80},
        "rentable": {"eq": True},
        "rented": {"eq": False}
    }
    
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    
    try:
        response = session.post(url, json=payload, headers=headers, timeout=12)
        if response.status_code == 200:
            offers = response.json().get("offers", [])
            results = []
            for offer in offers:
                gpu_name = offer.get("gpu_name", "")
                price_per_hr = offer.get("dph_total", 0.0) # Dollars per hour total
                dl_perf = offer.get("dlperf", 0.0)
                reliability = offer.get("reliability", 0.0)
                
                # Match against key target models
                for target in BENCHMARK_RATES.keys():
                    if target.lower() in gpu_name.lower():
                        results.append({
                            "provider": "Vast.ai",
                            "gpu": target,
                            "price_hr": round(price_per_hr, 3),
                            "reliability": round(reliability * 100, 1),
                            "dl_perf": round(dl_perf, 2)
                        })
            return results
        else:
            print(f"[WARN] Vast.ai returned status code {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"[WARN] Failed to fetch Vast.ai data: {e}")
        return []

def fetch_runpod_offers():
    """Queries RunPod GraphQL API using valid subfield selections for lowestPrice."""
    print("Fetching live market rates from RunPod...", flush=True)
    url = "https://api.runpod.io/graphql"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if RUNPOD_API_KEY:
        headers["Authorization"] = f"Bearer {RUNPOD_API_KEY}"

    # GraphQL query with required subfields under lowestPrice
    query = """
    query GpuTypes {
      gpuTypes {
        id
        displayName
        memoryInGb
        communityPrice
        securePrice
        lowestPrice {
          minimumBidPrice
          uninterruptablePrice
        }
      }
    }
    """
    
    try:
        response = requests.post(url, json={'query': query}, headers=headers, timeout=10)
        if response.status_code == 200:
            gpu_data = response.json().get("data", {}).get("gpuTypes", [])
            results = []
            for gpu in gpu_data:
                name = gpu.get("displayName", "")
                
                # Extract scalar values from lowestPrice subfields
                lowest_obj = gpu.get("lowestPrice") or {}
                lowest_price = lowest_obj.get("minimumBidPrice") or lowest_obj.get("uninterruptablePrice")
                
                price = lowest_price or gpu.get("communityPrice") or gpu.get("securePrice") or 0.0
                
                for target in BENCHMARK_RATES.keys():
                    if target.lower() in name.lower():
                        results.append({
                            "provider": "RunPod",
                            "gpu": target,
                            "price_hr": round(price, 3),
                            "reliability": 98.0, # RunPod default
                            "dl_perf": "N/A"
                        })
            return results
        else:
            print(f"[WARN] RunPod returned status code {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"[WARN] Failed to fetch RunPod data: {e}")
        return []

def analyze_arbitrage_opportunities():
    print("=" * 70)
    print("     GPU COMPUTE ARBITRAGE SCANNER -- VAST.AI & RUNPOD")
    print("=" * 70)
    
    vast_results = fetch_vast_ai_offers()
    runpod_results = fetch_runpod_offers()
    all_results = vast_results + runpod_results
    
    if not all_results:
        print("No compute market data retrieved.")
        return

    print("\n" + f"{'PROVIDER':<10} | {'GPU MODEL':<12} | {'SPOT PRICE/HR':<14} | {'BENCHMARK':<12} | {'POTENTIAL MARGIN'}")
    print("-" * 70)
    
    for item in all_results:
        gpu = item["gpu"]
        price = item["price_hr"]
        benchmark = BENCHMARK_RATES.get(gpu, 0.0)
        margin = benchmark - price
        margin_pct = (margin / benchmark * 100) if benchmark > 0 else 0.0
        
        # Calculate yield margin spread relative to benchmark
        status = f"+${margin:.2f}/hr ({margin_pct:.1f}%)" if margin > 0 else f"-${abs(margin):.2f}/hr"
        
        print(f"{item['provider']:<10} | {gpu:<12} | ${price:<13.3f} | ${benchmark:<11.2f} | {status}")
        
    print("-" * 70)
    print("Scan complete.\n")

if __name__ == "__main__":
    analyze_arbitrage_opportunities()