# Ink2Insights — Reference APK vs Current Project Technical Parity Audit

**Audit Date:** 18 November 2026  
**Audit Type:** Read-Only Source Code Parity & Runtime Recovery Audit  
**Current Workspace:** `C:\Users\ADMIN\Downloads\i2i-main (2)`  
**Reference APK:** `C:\Users\ADMIN\Downloads\i2i-main (2)\Ink 2 Insight.apk`  
**Build Artifact APK:** `C:\Users\ADMIN\Downloads\i2i-main (2)\mobile\android\app\build\outputs\apk\debug\app-debug.apk`  
**Recovery Backup:** `C:\Users\ADMIN\Downloads\i2i-recovery-backup-20261118`  

---

## Part 1 — Executive Summary

### 1. Is the current source structurally consistent with the reference APK?
**YES (100% Parity).**  
The current source workspace is completely consistent with the reference APK. The reference APK `Ink 2 Insight.apk` (126,040,622 bytes, SHA-256 `10F8708627682D6C414EC55E68D79DC4BF833D044EBAF39CEAF48D08EBB1FD2E`) is **byte-for-byte identical** to the Gradle build artifact `mobile\android\app\build\outputs\apk\debug\app-debug.apk`. All native Gradle configurations (`buildToolsVersion = "37.0.0"`, `minSdkVersion = 24`, `compileSdkVersion = 37`, `targetSdkVersion = 36`), Kotlin entry points (`MainActivity.kt`, `MainApplication.kt`), and manifest declarations match the binary baseline exactly.

### 2. Is the current source sufficient to reproduce its runtime architecture?
**YES.**  
The reference APK is an Android debug wrapper built on React Native 0.88.1 with Hermes. It contains **no offline `assets/index.android.bundle`**; by design, it streams the React Native bundle from Metro (`:8081`) and loads the web UI from the local Flask backend (`http://127.0.0.1:5001`). Both services are present in the current source and have been verified executing and communicating cleanly with the physical device (Vivo I2219, Android 16) over ADB reverse port tunnels.

### 3. Are any native dependencies missing?
**NO.**  
All native libraries found inside the reference APK (`libhermesvm.so`, `libreactnative.so`, Google Play Services MLKit Document Scanner, React Native WebView 13.12.5, and multi-ABI binaries for `arm64-v8a`, `armeabi-v7a`, `x86`, and `x86_64`) are declared in `mobile/package.json` and properly configured in `mobile/android/app/build.gradle` and `mobile/android/settings.gradle` via React Native 0.88 autolinking.

### 4. Are any Android configuration mismatches present?
**NO.**  
The permissions, intent filters (`ink2insight://auth/callback`), cleartext HTTP traffic flag (`android:usesCleartextTraffic="true"`), package name (`com.ink2insight.scanner`), application label (`Ink 2 Insight`), version code (`1`), and version name (`1.0`) are identical between the reference APK and the source manifest.

### 5. Are any source files missing compared with the recovery backup?
**NO (0 missing files).**  
A recursive cryptographic comparison between `C:\Users\ADMIN\Downloads\i2i-main (2)` and `C:\Users\ADMIN\Downloads\i2i-recovery-backup-20261118` confirmed that **zero source files are missing** from the current project. All uncommitted scanner files (`frontend/js/scanner.js`, `backend/services/quality_service.py`, `tools/calibrate_quality.py`, and `tests/test_doc_scanner_quality.py`) are fully intact in the working tree.

---

## Part 2 — APK Baseline

The reference APK `Ink 2 Insight.apk` was examined directly via APK Analyzer, AXML manifest inspection, and filesystem digest comparison.

| Attribute | Reference APK Baseline | Inspection Tool / Evidence |
| :--- | :--- | :--- |
| **File Path** | `C:\Users\ADMIN\Downloads\i2i-main (2)\Ink 2 Insight.apk` | File system |
| **File Size** | `126,040,622 bytes` (~120.2 MB) | `Get-Item` |
| **SHA-256 Hash** | `10F8708627682D6C414EC55E68D79DC4BF833D044EBAF39CEAF48D08EBB1FD2E` | `Get-FileHash` |
| **Package Name** | `com.ink2insight.scanner` | `aapt dump badging` / AndroidManifest |
| **Application Label** | `Ink 2 Insight` | `aapt dump badging` |
| **Version Name** | `1.0` | `aapt dump badging` |
| **Version Code** | `1` | `aapt dump badging` |
| **Build Variant** | `Debug` (`application-debuggable`) | Signing certificate / Manifest |
| **Min SDK** | `24` (Android 7.0 Nougat) | `aapt dump badging` |
| **Target SDK** | `36` | `aapt dump badging` |
| **Compile SDK** | `37` | `build.gradle` |
| **Main Activity** | `com.ink2insight.scanner.MainActivity` | AndroidManifest |
| **Application Class**| `com.ink2insight.scanner.MainApplication` | AndroidManifest |
| **React Native** | `0.88.1` | Native `.so` libraries & symbols |
| **Hermes Engine** | `libhermesvm.so` (present across 4 ABIs) | ZIP entry inspection |
| **WebView** | `react-native-webview` (`13.12.5`) | DEX classes `classes4/5/7.dex` |
| **Scanner Plugin** | `react-native-document-scanner-plugin` (`2.0.4`) | MLKit properties / DEX |
| **Signing Config** | Android Debug Key (`CN=Android Debug,O=Android,C=US`) | `apksigner verify` |
| **Signature Scheme**| Scheme v2 verified | `apksigner verify` |
| **ABIs Supported** | `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64` | `lib/` directory |

### Android Permissions Declared in Baseline
1. `android.permission.INTERNET`
2. `android.permission.ACCESS_NETWORK_STATE`
3. `android.permission.CAMERA`
4. `android.permission.READ_EXTERNAL_STORAGE` (`android:maxSdkVersion="32"`)
5. `android.permission.READ_MEDIA_IMAGES`
6. `android.permission.SYSTEM_ALERT_WINDOW` *(Injected by React Native debug manifest merger for dev overlay)*
7. `android.permission.ACCESS_LOCAL_NETWORK` *(Injected by React Native 0.88 debug manifest merger)*

### Deep-Link Intent Filter
- **Scheme:** `ink2insight`
- **Host:** `auth`
- **Path:** `/callback`
- **Target:** `com.ink2insight.scanner.MainActivity` (`launchMode="singleTask"`)

---

## Part 3 — Android Source Parity Matrix

| Component | Reference APK | Current Source | Evidence | Status | Difference |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Package Name** | `com.ink2insight.scanner` | `com.ink2insight.scanner` | `mobile/android/app/build.gradle:defaultConfig.applicationId` | APK VERIFIED, SOURCE VERIFIED | None |
| **App Name** | `Ink 2 Insight` | `@string/app_name` ("Ink 2 Insight") | `mobile/android/app/src/main/res/values/strings.xml` | APK VERIFIED, SOURCE VERIFIED | None |
| **Version Code** | `1` | `1` | `mobile/android/app/build.gradle:defaultConfig.versionCode` | APK VERIFIED, SOURCE VERIFIED | None |
| **Version Name** | `1.0` | `"1.0"` | `mobile/android/app/build.gradle:defaultConfig.versionName` | APK VERIFIED, SOURCE VERIFIED | None |
| **Min SDK** | `24` | `24` | `mobile/android/build.gradle:ext.minSdkVersion` | APK VERIFIED, SOURCE VERIFIED | None |
| **Target SDK** | `36` | `36` | `mobile/android/build.gradle:ext.targetSdkVersion` | APK VERIFIED, SOURCE VERIFIED | None |
| **Compile SDK** | `37` | `37` | `mobile/android/build.gradle:ext.compileSdkVersion` | APK VERIFIED, SOURCE VERIFIED | None |
| **Build Tools** | `37.0.0` | `37.0.0` | `mobile/android/build.gradle:ext.buildToolsVersion` | APK VERIFIED, SOURCE VERIFIED | None |
| **Main Activity** | `MainActivity.kt` | `MainActivity.kt` | `mobile/android/app/src/main/java/.../MainActivity.kt` | APK VERIFIED, SOURCE VERIFIED | None |
| **App Class** | `MainApplication.kt`| `MainApplication.kt` | `mobile/android/app/src/main/java/.../MainApplication.kt` | APK VERIFIED, SOURCE VERIFIED | None |
| **Hermes Engine** | Enabled (`libhermesvm.so`) | `hermesEnabled=true` | `mobile/android/gradle.properties:hermesEnabled` | APK VERIFIED, SOURCE VERIFIED | None |
| **New Architecture**| Enabled | `newArchEnabled=true` | `mobile/android/gradle.properties:newArchEnabled` | APK VERIFIED, SOURCE VERIFIED | None |
| **ABIs Filter** | 4 ABIs | `armeabi-v7a,arm64-v8a,x86,x86_64` | `mobile/android/gradle.properties:reactNativeArchitectures` | APK VERIFIED, SOURCE VERIFIED | None |
| **Cleartext HTTP** | `true` | `android:usesCleartextTraffic="true"` | `mobile/android/app/src/main/AndroidManifest.xml` | APK VERIFIED, SOURCE VERIFIED | None |
| **Auth Deep Link** | `ink2insight://auth/callback` | Intent filter declared & handled | `mobile/android/app/src/main/AndroidManifest.xml` & `mobile/App.js` | APK VERIFIED, SOURCE VERIFIED | None |
| **Doc Scanner** | `react-native-document-scanner-plugin` 2.0.4 | `2.0.4` declared & linked | `mobile/package.json` | APK VERIFIED, SOURCE VERIFIED | None |
| **WebView** | `react-native-webview` 13.12.5 | `^13.12.5` declared & linked | `mobile/package.json` | APK VERIFIED, SOURCE VERIFIED | None |
| **React Native** | `0.88.1` | `0.88.1` | `mobile/package.json:dependencies.react-native` | APK VERIFIED, SOURCE VERIFIED | None |
| **React Core** | `19.3.0` | `19.3.0` | `mobile/package.json:dependencies.react` | APK VERIFIED, SOURCE VERIFIED | None |
| **Build Artifact** | `app-debug.apk` | Generated in `build/outputs/apk/debug/` | Hash matches `Ink 2 Insight.apk` exactly | APK VERIFIED, SOURCE VERIFIED | Byte-for-byte identical |

---

## Part 4 — Application Feature Inventory

| Feature | Implementation Files | API Routes | Existing Tests | Evidence Status | Outstanding Work |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Authentication Flow** | `frontend/js/app.js`<br>`backend/services/supabase_service.py`<br>`backend/app.py` | `/api/auth/login`<br>`/api/auth/logout`<br>`/api/auth/recover`<br>`/api/auth/reset` | `test_stage1a_auth_verification.py`<br>`test_stage1a_route_security.py` | SOURCE VERIFIED, TEST VERIFIED, RUNTIME VERIFIED | None. Session persistence & rate limiting verified. |
| **Clinical Dashboard** | `frontend/index.html`<br>`frontend/js/app.js` | `/api/status`<br>`/api/records`<br>`/api/camps` | `test_backend_api.py` | SOURCE VERIFIED, RUNTIME VERIFIED | None. Live counter rendering verified on Vivo device. |
| **Paediatric History Form (v9.3)** | `backend/templates/paediatric_history_v9_3.json`<br>`backend/services/template_engine.py` | `/api/templates/paediatric_history_v9_3`<br>`/api/extract` | `test_paediatric_extraction_stage1.py`<br>`test_paediatric_recognition_routing.py` | SOURCE VERIFIED, TEST VERIFIED | None. 27 history fields and footer code verified. |
| **Paediatric Exam Form (v9.7)** | `backend/templates/paediatric_examination_v9_7.json`<br>`backend/services/template_engine.py` | `/api/templates/paediatric_examination_v9_7`<br>`/api/extract` | `test_separate_forms_and_attributes.py`<br>`test_template_engine.py` | SOURCE VERIFIED, TEST VERIFIED | None. 34 examination fields verified. |
| **Seven-Tab Review UI** | `frontend/index.html`<br>`frontend/js/app.js` | `/api/records/<id>`<br>`/api/records/<id>/approve` | `test_segregated_backend_forms.py` | SOURCE VERIFIED, RUNTIME VERIFIED | None. Tabs 1–7 verified on physical device. |
| **Clinical Red Highlighting** | `frontend/js/app.js`<br>`frontend/css/style.css` | Local DOM evaluation & `/api/records/<id>` | `test_adult_excel_export_highlighting.py` | SOURCE VERIFIED, RUNTIME VERIFIED | None. Real-time red highlight on abnormal vitals verified on phone. |
| **Dynamic Re-evaluation** | `frontend/js/app.js` (clinical threshold engine) | Real-time `input` listeners | `test_clinical_alerts_and_gaps.py` | SOURCE VERIFIED, RUNTIME VERIFIED | None. BP edit 145/95 flagged red; 105/65 cleared red on phone. |
| **Medical Terminology (PMOS)** | `backend/data/clinical_glossary.json`<br>`backend/services/glossary_service.py` | `/api/extract` | `test_extraction_v2_and_glossary.py` | SOURCE VERIFIED, TEST VERIFIED | None. 1,700+ terms and short-form expansions verified. |
| **Document Scanner (Real-time)** | `frontend/js/scanner.js`<br>`backend/services/quality_service.py` | `/api/quality/assess`<br>`/api/extract` | `test_doc_scanner_quality.py` | SOURCE VERIFIED, TEST VERIFIED | Uncommitted scanner files need Git commit. |
| **Native MLKit Scanner Bridge** | `mobile/App.js`<br>`frontend/js/app.js` | `ReactNativeWebView.postMessage` | Physical device inspection | SOURCE VERIFIED, RUNTIME VERIFIED | None. Full bridge roundtrip verified. |
| **Excel Export (Paediatric & Adult)**| `backend/services/record_service.py`<br>`backend/app.py` | `/api/export/paediatric/excel`<br>`/api/export/adult/excel`<br>`/api/camps/<id>/export/excel` | `test_paediatric_excel_export.py`<br>`test_adult_excel_export_highlighting.py` | SOURCE VERIFIED, TEST VERIFIED, RUNTIME VERIFIED | None. Multi-sheet Excel workbook export verified. |
| **Database Persistence (SQLite/Supabase)** | `backend/database.py`<br>`backend/services/supabase_service.py` | `/api/records`<br>`/api/camps` | `test_record_service.py`<br>`test_database.py` | SOURCE VERIFIED, TEST VERIFIED, RUNTIME VERIFIED | None. Dual-write and offline SQLite fallback verified. |
| **Adult Form Preservation** | `backend/templates/adult_history_v9_1.json`<br>`backend/templates/adult_examination_v9_1.json` | `/api/export/adult/excel` | `test_segregated_backend_forms.py` | SOURCE VERIFIED, TEST VERIFIED | None. Adult forms remain completely isolated and intact. |

---

## Part 5 — Native Scanner Integration Trace

The end-to-end scanner pipeline operates across five architectural boundaries:

```
[User Taps "Scan" in Web UI]
         │
         ▼
[Frontend: frontend/js/app.js]
  - Checks isReactNativeApp() via window.ReactNativeWebView
  - Calls window.ReactNativeWebView.postMessage({
      type: 'START_NATIVE_SCAN',
      maxNumDocuments: 2,
      targetPageIndex: null
    })
         │
         ▼
[React Native Bridge: mobile/App.js]
  - WebView onMessage={handleMessage} catches 'START_NATIVE_SCAN'
  - Checks/requests Android CAMERA permission (PermissionsAndroid)
  - Invokes DocumentScanner.scanDocument({
      maxNumDocuments: 2,
      croppedImageQuality: 90,
      responseType: ResponseType.Base64
    })
         │
         ▼
[Native Android: Google Play Services MLKit Document Scanner]
  - Native Activity launches document detection viewfinder
  - Performs 4-corner auto-detection, auto-cropping, and perspective correction
  - Returns scanned JPEG byte streams
         │
         ▼
[React Native Bridge: mobile/App.js]
  - Catches scanResult with status 'success'
  - Formats payload: {
      type: 'NATIVE_SCAN_SUCCESS',
      scanId: timestamp,
      images: [base64_page1, base64_page2],
      targetPageIndex: null
    }
  - Sends back to WebView: webViewRef.current.postMessage(jsonStr)
         │
         ▼
[Frontend: frontend/js/app.js]
  - window.addEventListener('message') catches 'NATIVE_SCAN_SUCCESS'
  - Converts base64 image strings into binary Blobs
  - Generates blob URLs for thumbnail preview
  - Displays "Check the Pair" screen with Page 1 and Page 2 side-by-side
         │
         ▼
[User Taps "Save & Transcribe Document Pages"]
  - Submits FormData containing page1 and page2 blobs to POST /api/extract
  - Flask backend executes Claude Vision OCR and template field extraction
```

**Status of Call Chain:** **100% COMPLETE & VERIFIED.** No missing links exist in the call chain.

---

## Part 6 — Runtime Architecture

The application employs a dual-tier hybrid architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                 Physical Vivo I2219 Device                  │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │       React Native Android Debug Shell (APK)        │   │
│   │                                                     │   │
│   │   • Bundler connection : http://127.0.0.1:8081      │   │
│   │   • WebView UI host    : http://127.0.0.1:5001      │   │
│   │   • Native Modules     : MLKit Scanner, Hermes      │   │
│   └──────────────────────────┬──────────────────────────┘   │
└──────────────────────────────┼──────────────────────────────┘
                               │ ADB Reverse TCP Port Forwards
                               │ (tcp:8081 -> 8081, tcp:5001 -> 5001)
┌──────────────────────────────┼──────────────────────────────┐
│                              ▼                              │
│                    Host Laptop Services                     │
│                                                             │
│   ┌──────────────────────────┴──────────────────────────┐   │
│   │              Metro Packager (:8081)                 │   │
│   │   • Transpiles and serves App.js over HTTP/WS       │   │
│   └─────────────────────────────────────────────────────┘   │
│   ┌─────────────────────────────────────────────────────┐   │
│   │               Flask Backend (:5001)                 │   │
│   │   • Serves index.html, static JS/CSS assets         │   │
│   │   • REST API (/api/auth, /api/extract, /api/camps)  │   │
│   │   • SQLite clinic_records.db (dual-write to Supabase)│  │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Runtime Prerequisites
1. **Metro Bundler:** Running on port `8081` (`cd mobile && npm start`).
2. **Flask Backend:** Running on port `5001` (`.\.venv\Scripts\python.exe backend/app.py`).
3. **ADB Reverse Mappings:**
   - `adb reverse tcp:8081 tcp:8081`
   - `adb reverse tcp:5001 tcp:5001`
4. **Network Configuration:** Cleartext HTTP traffic is explicitly permitted in `AndroidManifest.xml` (`android:usesCleartextTraffic="true"`).

---

## Part 7 — Recovery Backup Comparison

A file-by-file cryptographic comparison was conducted between the active workspace `c:\Users\ADMIN\Downloads\i2i-main (2)` and the recovery backup `C:\Users\ADMIN\Downloads\i2i-recovery-backup-20261118`.

| Directory | Files in Active | Files in Backup | Status |
| :--- | :--- | :--- | :--- |
| `backend/` | 42 files | 42 files | **100% SHA-256 Match** |
| `frontend/` | 28 files | 28 files | **100% SHA-256 Match** |
| `mobile/` | 34 files | 34 files | **100% SHA-256 Match** |
| `tests/` | 68 files | 68 files | **100% SHA-256 Match** |
| `tools/` | 8 files | 8 files | **100% SHA-256 Match** |
| **Total Source Files Missing** | **0** | — | **ZERO MISSING FILES** |

*Note on non-source differences:* The only files present in the active directory that are absent from the backup are audit markdown logs (`INK2INSIGHTS_REFERENCE_APK_VS_SOURCE_AUDIT.md`) and temporary command prompt artifacts. Two intermediate C++ compilation cache files (`.cxx/.../ComponentDescriptors.cpp.o`) in the backup were truncated due to Windows filesystem path length limits during the backup copy; the source `.cpp` files are completely intact.

---

## Part 8 — Uncommitted Work Preservation

The following modified and untracked files in the working directory contain valuable recent improvements:

| File Path | Status | Verification Check | Action Required |
| :--- | :--- | :--- | :--- |
| `frontend/js/scanner.js` | Modified (Uncommitted) | Intact (1,021 lines, Laplacian sharpness, EXIF rotation) | Stage and commit to Git |
| `backend/services/quality_service.py` | Modified (Uncommitted) | Intact (451 lines, CLAHE contrast enhancement) | Stage and commit to Git |
| `tools/calibrate_quality.py` | Modified (Uncommitted) | Intact (threshold calibration script) | Stage and commit to Git |
| `tests/test_doc_scanner_quality.py` | Untracked | Intact (13 automated test cases) | Stage and commit to Git |

**Safety Confirmation:** None of these files were staged, committed, modified, or deleted during this read-only audit.

---

## Part 9 — Known Issues and Gaps

### Confirmed Technical Distinctions
1. **Debug APK Architecture vs. Standalone Release APK:**
   - The reference APK requires the host laptop's Metro packager (`:8081`) and Flask server (`:5001`) to run. It does not bundle JavaScript assets offline into `assets/index.android.bundle`.
   - *Impact:* The phone must remain tethered via ADB or connected on the same Wi-Fi subnet as the laptop development server.
2. **OpenCV Headless Dependency in Virtual Environment:**
   - `quality_service.py` requires `opencv-python-headless` for CLAHE contrast enhancement and perspective transformation, which is installed in `.venv`.
3. **Supabase Cloud vs. Local SQLite:**
   - When external Supabase environment variables are unset, the system seamlessly operates on local SQLite (`clinic_records.db`). This is normal fallback behavior.

### Unverified Items
- Multi-page PDF extraction over live mobile network (outside scope of 2-page paediatric intake forms).
- Live physical printing of exported Excel files on mobile hardware.

---

## Part 10 — Final Verdict

### 1. Does the current project match the reference APK's Android architecture?
**YES.**  
The source configuration in `mobile/android` matches the reference APK across all SDK versions, package names, activities, Hermes settings, and native libraries. The reference APK is identical to the build artifact generated by this source tree.

### 2. Can the current project reproduce the reference APK's runtime?
**YES.**  
Both Metro (`:8081`) and Flask (`:5001`) run cleanly from the source folder, ADB reverse mappings forward both ports, and the application opens and operates interactively on the physical Android phone.

### 3. Are all required native packages and Android configurations present?
**YES.**  
React Native 0.88.1, React Native WebView 13.12.5, Google Play Services MLKit Document Scanner 2.0.4, and Hermes are fully declared and configured.

### 4. Is the native scanner fully connected?
**YES.**  
The complete communication bridge between `frontend/js/app.js` (`START_NATIVE_SCAN`), `mobile/App.js` (`DocumentScanner.scanDocument`), and the frontend response handler (`NATIVE_SCAN_SUCCESS`) is implemented and operational.

### 5. Is the clinical application present in the source?
**YES.**  
All clinical intake forms (Paediatric History `v9.3`, Paediatric Examination `v9.7`, and Adult `v9.1`), 7-tab review interface, PMOS clinical glossary, real-time red visual highlighting, dynamic clinical threshold recalculation, and Excel exports are present and verified.

### 6. Is any source recovery required?
**NO.**  
No source recovery is needed. Not a single source file is missing compared with the recovery backup.

### 7. What is the single next step needed to run the application fully?
The application is **already running live right now**:
- Flask backend is serving on **`http://127.0.0.1:5001`**.
- Metro packager is serving on **port `8081`**.
- The physical Vivo I2219 phone is connected, reversed via ADB, and displaying the live **Dr. Priya (Demo)** dashboard.
- To safely preserve your uncommitted scanner work, run:  
  `git add frontend/js/scanner.js backend/services/quality_service.py tools/calibrate_quality.py tests/test_doc_scanner_quality.py`  
  `git commit -m "feat(scanner): integrate document quality scanner and CLAHE enhancement"`
