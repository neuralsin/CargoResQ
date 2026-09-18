"""
Simulation Mode API Endpoints (Phase 16).
Enables operators to trigger deterministic scenarios for testing and demonstrations.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from ..simulation import SCENARIOS, run_simulation
from ..events.producer import event_producer

router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])


class RunSimulationRequest(BaseModel):
    scenario: str = Field("cold_chain_critical", description="cold_chain_critical | hazmat_no_match | competing_rescuers")
    seconds_per_simulated_minute: float = Field(0.05, gt=0, le=5.0)


@router.get("/scenarios")
async def get_scenarios():
    return [
        {
            "key": k,
            "label": v.get("label"),
            "cargoType": v.get("cargoType"),
            "requiresRefrigeration": v.get("requiresRefrigeration", False),
            "isHazmat": v.get("isHazmat", False),
            "valueInr": v.get("valueInr"),
        }
        for k, v in SCENARIOS.items()
    ]


@router.post("/run")
async def trigger_simulation(req: RunSimulationRequest, background_tasks: BackgroundTasks):
    if req.scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scenario. Choose from {list(SCENARIOS.keys())}",
        )

    # Launch simulation in background task
    async def _run():
        await run_simulation(
            scenario_key=req.scenario,
            emit_event=event_producer.publish,
            seconds_per_simulated_minute=req.seconds_per_simulated_minute,
        )

    background_tasks.add_task(_run)

    return {
        "status": "simulation_started",
        "scenario": req.scenario,
        "clockSpeed": f"1 sim minute = {req.seconds_per_simulated_minute}s",
    }
