"""Render high-resolution screenshots of all Admin and Mobile app pages.

Saves real rendered PNG images into the `renders/` folder.
"""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("renders")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://localhost:8000"


def render_all():
    print(f"Starting page renderer... Output folder: {OUTPUT_DIR.resolve()}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ==========================================
        # 1. ADMIN PAGES (Desktop Viewport: 1440 x 900)
        # ==========================================
        print("\n--- Rendering Admin Pages ---")
        admin_page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        # Admin 01: Full Dashboard Overview
        admin_page.goto(f"{BASE_URL}/", wait_until="networkidle")
        time.sleep(1)
        path1 = OUTPUT_DIR / "admin_01_dashboard_overview.png"
        admin_page.screenshot(path=str(path1))
        print(f"Saved: {path1.name}")

        # Admin 02: Interactive Live Map
        admin_page.evaluate("if (typeof toggleMapMode === 'function') toggleMapMode();")
        time.sleep(1.5)
        path2 = OUTPUT_DIR / "admin_02_interactive_live_map.png"
        admin_page.screenshot(path=str(path2))
        print(f"Saved: {path2.name}")

        # Switch back to vector map
        admin_page.evaluate("if (typeof toggleMapMode === 'function') toggleMapMode();")
        time.sleep(0.5)

        # Admin 03: Dijkstra Route Optimizer Modal
        admin_page.evaluate("if (typeof openDijkstraModal === 'function') openDijkstraModal();")
        time.sleep(0.8)
        path3 = OUTPUT_DIR / "admin_03_dijkstra_optimizer_modal.png"
        admin_page.screenshot(path=str(path3))
        print(f"Saved: {path3.name}")
        admin_page.evaluate("if (typeof closeDijkstraModal === 'function') closeDijkstraModal();")

        # Admin 04: Porter Fleet Fare Estimator Modal
        admin_page.evaluate("if (typeof openPorterModal === 'function') openPorterModal();")
        time.sleep(0.8)
        path4 = OUTPUT_DIR / "admin_04_porter_fare_estimator_modal.png"
        admin_page.screenshot(path=str(path4))
        print(f"Saved: {path4.name}")
        admin_page.evaluate("if (typeof closePorterModal === 'function') closePorterModal();")

        # Admin 05: Company Fulfilment Rating & Metrics Drawer
        admin_page.evaluate("if (typeof openFulfilmentDrawer === 'function') openFulfilmentDrawer();")
        time.sleep(0.8)
        path5 = OUTPUT_DIR / "admin_05_fulfilment_ratings_drawer.png"
        admin_page.screenshot(path=str(path5))
        print(f"Saved: {path5.name}")
        admin_page.evaluate("if (typeof closeFulfilmentDrawer === 'function') closeFulfilmentDrawer();")

        # Admin 06: Company Administrator Login Modal
        admin_page.evaluate("if (typeof openLoginModal === 'function') openLoginModal();")
        time.sleep(0.8)
        path6 = OUTPUT_DIR / "admin_06_company_admin_login.png"
        admin_page.screenshot(path=str(path6))
        print(f"Saved: {path6.name}")
        admin_page.evaluate("if (typeof closeLoginModal === 'function') closeLoginModal();")

        admin_page.close()

        # ==========================================
        # 2. UBER 1:1 MOBILE APP INTERFACE (Viewport: 412 x 892)
        # ==========================================
        print("\n--- Rendering Uber 1:1 Mobile App Interface ---")
        uber_file_url = f"file:///{Path('static/uber_driver.html').resolve().as_posix()}"
        mobile_page = browser.new_page(viewport={"width": 412, "height": 892}, device_scale_factor=2)
        mobile_page.goto(uber_file_url, wait_until="networkidle")
        time.sleep(1)

        # Mobile 01: Welcome & Login Screen
        mobile_page.evaluate("switchTab('auth');")
        time.sleep(0.5)
        path_m1 = OUTPUT_DIR / "mobile_01_welcome_login.png"
        mobile_page.screenshot(path=str(path_m1))
        print(f"Saved: {path_m1.name}")

        # Mobile 02: 1:1 Uber Home Screen
        mobile_page.evaluate("switchTab('home');")
        time.sleep(0.5)
        path_m2 = OUTPUT_DIR / "mobile_02_home_dispatch.png"
        mobile_page.screenshot(path=str(path_m2))
        print(f"Saved: {path_m2.name}")

        # Mobile 03: 1-Touch Breakdown SOS Dialog
        mobile_page.evaluate("openSosModal();")
        time.sleep(0.5)
        path_m3 = OUTPUT_DIR / "mobile_03_breakdown_sos_dialog.png"
        mobile_page.screenshot(path=str(path_m3))
        print(f"Saved: {path_m3.name}")
        mobile_page.evaluate("closeModal('modalSos');")

        # Mobile 04: Services & Porter Fleet Estimator Screen
        mobile_page.evaluate("switchTab('services');")
        time.sleep(0.5)
        path_m4 = OUTPUT_DIR / "mobile_04_services_porter_estimator.png"
        mobile_page.screenshot(path=str(path_m4))
        print(f"Saved: {path_m4.name}")

        # Mobile 05: Activity History Screen
        mobile_page.evaluate("switchTab('activity');")
        time.sleep(0.5)
        path_m5 = OUTPUT_DIR / "mobile_05_activity_history.png"
        mobile_page.screenshot(path=str(path_m5))
        print(f"Saved: {path_m5.name}")

        # Mobile 06: Dark Activity Filter Bottom Sheet (1:1 with activite-filtre-screen.jpg)
        mobile_page.evaluate("openFilterSheet();")
        time.sleep(0.5)
        path_m6 = OUTPUT_DIR / "mobile_06_activity_dark_filter_sheet.png"
        mobile_page.screenshot(path=str(path_m6))
        print(f"Saved: {path_m6.name}")
        mobile_page.evaluate("closeModal('sheetFilter');")

        # Mobile 07: Account, Safety & Earnings Screen
        mobile_page.evaluate("switchTab('account');")
        time.sleep(0.5)
        path_m7 = OUTPUT_DIR / "mobile_07_account_safety_earnings.png"
        mobile_page.screenshot(path=str(path_m7))
        print(f"Saved: {path_m7.name}")

        # Mobile 08: Pallet Handover QR Verification Dialog
        mobile_page.evaluate("switchTab('home'); openPalletModal();")
        time.sleep(0.5)
        path_m8 = OUTPUT_DIR / "mobile_08_pallet_qr_verification.png"
        mobile_page.screenshot(path=str(path_m8))
        print(f"Saved: {path_m8.name}")
        mobile_page.evaluate("closeModal('modalPallet');")

        mobile_page.close()

        # ==========================================
        # 3. WEB TELEMETRY DRIVER APP (http://localhost:8000/mobile)
        # ==========================================
        print("\n--- Rendering Web Telemetry Driver Screens ---")
        telem_page = browser.new_page(viewport={"width": 412, "height": 892}, device_scale_factor=2)
        telem_page.goto(f"{BASE_URL}/mobile", wait_until="networkidle")
        time.sleep(1)

        # Tab 1: Dispatch Hub
        path_t1 = OUTPUT_DIR / "mobile_telemetry_01_dispatch_hub.png"
        telem_page.screenshot(path=str(path_t1))
        print(f"Saved: {path_t1.name}")

        # Tab 2: Rescue Radar Tracking
        telem_page.evaluate("if (typeof switchTab === 'function') switchTab('radar');")
        time.sleep(0.8)
        path_t2 = OUTPUT_DIR / "mobile_telemetry_02_rescue_radar.png"
        telem_page.screenshot(path=str(path_t2))
        print(f"Saved: {path_t2.name}")

        # Tab 3: Active Map Telemetry
        telem_page.evaluate("if (typeof switchTab === 'function') switchTab('telemetry');")
        time.sleep(0.8)
        path_t3 = OUTPUT_DIR / "mobile_telemetry_03_active_telemetry.png"
        telem_page.screenshot(path=str(path_t3))
        print(f"Saved: {path_t3.name}")

        # Tab 4: Driver Profile & Earnings
        telem_page.evaluate("if (typeof switchTab === 'function') switchTab('profile');")
        time.sleep(0.8)
        path_t4 = OUTPUT_DIR / "mobile_telemetry_04_driver_profile.png"
        telem_page.screenshot(path=str(path_t4))
        print(f"Saved: {path_t4.name}")

        telem_page.close()

        browser.close()

    print("\nAll real images rendered successfully into:", OUTPUT_DIR.resolve())


if __name__ == "__main__":
    render_all()
