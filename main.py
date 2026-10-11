import os
import time
import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
from pydantic import BaseModel
from dotenv import load_dotenv

# Explicitly load environment variables from full path
load_dotenv("/opt/apc-gpu-orchestrator/.env")

app = FastAPI(title="APC On-Demand GPU Orchestrator")

VAST_API_KEY = os.getenv("VAST_AI_API_KEY", "")
API_AUTH_TOKEN = os.getenv("SERVER_AUTH_TOKEN", "")

class JobPayload(BaseModel):
    job_id: str
    script_command: str
    max_price_per_hr: float

def execute_gpu_lifecycle(job_id: str, script_command: str, max_price_per_hr: float):
    print(f"[ORCHESTRATOR] Initializing Job ID: {job_id}", flush=True)
    
    url = "https://console.vast.ai/api/v0/bundles"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if VAST_API_KEY:
        headers["Authorization"] = f"Bearer {VAST_API_KEY}"
        
    # Search query payload matching scanner criteria
    payload = {
        "limit": 100,
        "type": "on-demand",
        "reliability": {"gte": 0.80},
        "rentable": {"eq": True},
        "rented": {"eq": False}
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=12)
        if res.status_code != 200:
            print(f"❌ [JOB FAILED] Vast.ai API error: {res.status_code} - {res.text}", flush=True)
            return
            
        offers = res.json().get("offers", [])
        
        # Substring filter for RTX 4090 spot listings
        candidate_offers = []
        for o in offers:
            gpu_name = o.get("gpu_name", "").lower()
            price = o.get("dph_total", 999.0)
            
            if ("4090" in gpu_name or "rtx 4090" in gpu_name) and price <= max_price_per_hr:
                candidate_offers.append(o)
                
        if not candidate_offers:
            print(f"❌ [JOB FAILED] No matching GPUs under ${max_price_per_hr}/hr.", flush=True)
            return
            
        best_offer = min(candidate_offers, key=lambda x: x.get("dph_total", 999.0))
        offer_id = best_offer["id"]
        chosen_price = best_offer["dph_total"]
        gpu_model = best_offer.get("gpu_name")
        
        print(f"✔ Target Instance Found: {gpu_model} (Offer ID {offer_id}) @ ${chosen_price:.3f}/hr", flush=True)
        
        # Provision instance with GitHub clone bootstrap
        PUT_URL = f"https://console.vast.ai/api/v0/asks/{offer_id}/"
        put_payload = {
            "client_id": "me",
            "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
            "disk": 10.0,
            "onstart": f"bash -c 'apt-get update && apt-get install -y git && {script_command}'"
        }
        
        create_res = requests.put(PUT_URL, json=put_payload, headers=headers, timeout=12)
        if create_res.status_code == 200:
            contract_id = create_res.json().get("new_contract")
            print(f"✔ Instance Provisioned. Contract ID: {contract_id}", flush=True)
            
            # Wait window before safety teardown
            time.sleep(15)
            
            # Teardown sequence
            del_res = requests.delete(f"https://console.vast.ai/api/v0/instances/{contract_id}/", headers=headers)
            if del_res.status_code == 200:
                print(f"✔ [TEARDOWN COMPLETE] Instance {contract_id} terminated.", flush=True)
            else:
                print(f"⚠️ [TEARDOWN WARNING] Could not terminate instance {contract_id}: {del_res.status_code}", flush=True)
        else:
            print(f"❌ [PROVISION ERROR] {create_res.status_code}: {create_res.text}", flush=True)
            
    except Exception as e:
        print(f"❌ [ORCHESTRATOR ERROR] {e}", flush=True)

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "APC GPU Orchestrator"}

@app.post("/api/v1/dispatch-job")
def dispatch_job(payload: JobPayload, background_tasks: BackgroundTasks, x_auth_token: str = Header(None)):
    if x_auth_token != API_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized request")
        
    background_tasks.add_task(
        execute_gpu_lifecycle,
        payload.job_id,
        payload.script_command,
        payload.max_price_per_hr
    )
    return {
        "status": "queued",
        "job_id": payload.job_id,
        "message": "GPU provisioning initiated asynchronously."
    }