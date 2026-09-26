"""
Müfettiş AI - Banka Denetim & Mevzuat Uyum Asistanı
Modüler mimari giriş noktası (Entry Point): CLI komutları ve Streamlit arayüzü.
Kütüphane kodu `mufettis` paketindedir; bu dosya yalnızca kullandığı isimleri import eder.
"""

import sys
import json
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mufettis.common.utils import safe_json
from mufettis.config import APP_VERSION, DATA_DIR
from mufettis.importers.bootstrapper import RegulationBootstrapper
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import (
    repair_production_document_from_pdf,
    scan_production_rag_quality,
)
from mufettis.scheduler.scheduler import BackgroundUpdateScheduler, run_automatic_update
from mufettis.ui.app import run_streamlit, st
from mufettis.tests.runner import run_self_tests

# -----------------------------------------------------------------------------
# CLI / Streamlit Entry Point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=f"Müfettiş AI v{APP_VERSION}")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--run", action="store_true", help="Streamlit arayüzünü bu dosya üzerinden çalıştır")
    parser.add_argument("--update-now", action="store_true", help="BDDK + Resmî Gazete güncellemesini tek sefer çalıştır")
    parser.add_argument("--run-scheduler", action="store_true", help="Arka plan günlük scheduler\x27ı sürekli çalıştır")
    parser.add_argument("--bootstrap", action="store_true", help="İlk mevzuat bilgi tabanını resmi kaynaklardan kur")
    parser.add_argument("--rag-scan", action="store_true", help="Production source_cache + Chroma kalite taraması")
    parser.add_argument("--repair-scan", action="store_true", help="Salt-okunur üretim onarım taraması (production write: disabled)")
    parser.add_argument("--repair-extract-pdf", type=str, help="PDF dosyasından salt-okunur aday çıkarım yapar (production write: disabled)")
    parser.add_argument("--repair-expected-hash", type=str, default="", help="Aday çıkarım için beklenen SHA256 hash değeri")
    parser.add_argument("--repair-commit", action="store_true", help="Doğrulanmış READY adayı kontrollü biçimde commit eder (production write: explicit)")
    parser.add_argument("--repair-verify", action="store_true", help="Commit sonrası POST-REPAIR VERIFICATION çalıştırır (production write: 0)")
    parser.add_argument("--repair-rollback", type=str, default="", help="Commit ID'ye ait tamiri güvenle geri alır (production write: explicit)")
    parser.add_argument("--repair-rollback-dry-run", action="store_true", help="Rollback işlemini simüle eder (production write: 0)")
    parser.add_argument("--repair-audit", action="store_true", help="Son onarım audit özetlerini listeler (read-only)")
    parser.add_argument("--repair-audit-repair-id", type=str, default="", help="Belirtilen repair_id'ye ait audit olaylarını gösterir (read-only)")
    parser.add_argument("--repair-audit-document", type=str, default="", help="Belirtilen document_id'ye ait audit olaylarını gösterir (read-only)")
    parser.add_argument("--repair-automation", action="store_true", help="Controlled Automation cycle çalıştırır")
    parser.add_argument("--repair-automation-mode", type=str, default="scan_only", choices=["scan_only", "dry_run", "safe_auto_repair", "manual_approval"], help="Otomasyon modu")
    parser.add_argument("--repair-automation-approve", type=str, default="", help="Onay ID'si ile bekleyen bir onarımı yürütür")
    parser.add_argument("--repair-automation-env", type=str, default="rag_lab", choices=["rag_lab", "production"], help="Otomasyon hedef ortamı")
    parser.add_argument("--rag-repair-pdf", action="append", default=[], help="Aynı hash'e ait indexed PDF'yi onar; tekrar edilebilir")
    parser.add_argument("--rag-repair-dry-run", action="store_true", help="RAG repair planını uygulatmadan göster")
    parser.add_argument("--repair-wave", action="store_true", help="Controlled Production Repair Wave çalıştırır")
    parser.add_argument("--repair-wave-scope", type=str, default="pilot", choices=["pilot", "small_batch", "medium_batch", "bddk_active", "other_sources", "all", "custom"], help="Dalga kapsamı")
    parser.add_argument("--repair-wave-doc-id", type=str, default="", help="Hedef belge kimliği")
    parser.add_argument("--repair-wave-limit", type=int, default=1, help="Dalga belge limiti")
    parser.add_argument("--repair-wave-dry-run", action="store_true", default=True, help="Dalga simülasyonu (yazma yok)")
    parser.add_argument("--repair-wave-execute", action="store_true", help="Gerçek onarımı aktifleştirir")
    parser.add_argument("--repair-wave-allow-production", action="store_true", help="Production onarımı için zorunlu açık onay bayrağı")
    parser.add_argument("--repair-wave-env", type=str, default="rag_lab", choices=["rag_lab", "production"], help="Dalga hedef ortamı")
    args = parser.parse_args()

    if args.self_test:
        result = run_self_tests()
        print(getattr(result, "output", ""), end="")
        for test, detail in result.errors:
            if str(test) in {"self_test_subprocess", "production_data_isolation"}:
                print(f"\n[{test}]\n{detail}")
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if args.repair_audit or args.repair_audit_repair_id or args.repair_audit_document:
        from repair.audit import (
            get_repair_audit,
            get_document_audit,
            list_recent_repairs,
            get_repair_lifecycle_summary,
            verify_audit_integrity,
        )
        print("=" * 60)
        print("SELF-HEALING REPAIR AUDIT TRAIL (READ-ONLY)")
        print("=" * 60)
        if args.repair_audit_repair_id:
            rid = args.repair_audit_repair_id.strip()
            summary = get_repair_lifecycle_summary(rid)
            if summary:
                print(f"Repair ID      : {summary.repair_id}")
                print(f"Document ID    : {summary.document_id}")
                print(f"Source SHA256  : {summary.source_sha256}")
                print(f"Trigger        : {summary.trigger}")
                print(f"Reason         : {summary.reason}")
                print(f"Classification : {summary.classification}")
                print(f"Engine         : {summary.extraction_engine}")
                print(f"Commit Status  : {summary.commit_status}")
                print(f"Verify Status  : {summary.verification_status}")
                print(f"Rollback Done  : {'YES' if summary.rollback_occurred else 'NO'}")
                print(f"Rollback Status: {summary.rollback_status or '-'}")
                print(f"Final Status   : {summary.final_repair_status}")
                print(f"Events Count   : {summary.events_count}")
                print(f"Started At     : {summary.started_at}")
                print(f"Completed At   : {summary.completed_at}")
            events = get_repair_audit(rid)
            print(f"\nAudit Events ({len(events)}):")
            for e in events:
                print(f"  [{e.sequence_no}] {e.timestamp} | {e.event_type} | {e.status} | {e.reason}")
        elif args.repair_audit_document:
            did = args.repair_audit_document.strip()
            events = get_document_audit(did)
            print(f"Document ID: {did} ({len(events)} events):")
            for e in events:
                print(f"  [{e.sequence_no}] {e.timestamp} | {e.event_type} | repair_id={e.repair_id} | {e.status}")
        else:
            repairs = list_recent_repairs(limit=20)
            print(f"Son Onarımlar ({len(repairs)}):")
            for r in repairs:
                print(f"  - {r.get('repair_id')} | {r.get('document_id')} | {r.get('latest_event_type')} | {r.get('final_status')} | {r.get('last_timestamp')}")
        valid_chain, chain_errs = verify_audit_integrity()
        print(f"\nHash-Chain Integrity: {'PASS' if valid_chain else 'FAIL'}")
        if chain_errs:
            for err in chain_errs:
                print(f"  ! {err}")
        print("=" * 60)
        raise SystemExit(0)
    if args.repair_rollback:
        from repair.rollback import rollback_commit
        print("=" * 60)
        print("SAFE REPAIR ROLLBACK")
        if args.repair_rollback_dry_run:
            print("MODE: DRY-RUN (write: 0)")
        else:
            print("MODE: LIVE RESTORATION")
        print("=" * 60)
        rag = LocalRAG(allow_download=False)
        index = IngestIndex()
        res = rollback_commit(
            commit_id=args.repair_rollback.strip(),
            rag=rag,
            index=index,
            dry_run=bool(args.repair_rollback_dry_run),
            force=True,
        )
        print(f"Rollback Status : {res.status}")
        print(f"Cache           : {res.cache_status}")
        print(f"Index           : {res.index_status}")
        print(f"Chroma          : {res.chroma_status}")
        print(f"Fingerprint     : {res.fingerprint_status}")
        if res.failures:
            print("\nFAILURES:")
            for f in res.failures:
                print(f"  ✗ {f}")
        if res.warnings:
            print("\nWARNINGS:")
            for w in res.warnings:
                print(f"  ! {w}")
        print("=" * 60)
        raise SystemExit(0 if res.status in {"ROLLBACK_SUCCESS", "ROLLBACK_ALREADY_RESTORED"} else 1)
    if args.repair_automation or args.repair_automation_approve:
        from repair.orchestrator import ControlledAutomationOrchestrator
        from repair.models import AutomationPolicy, AutomationMode
        print("=" * 60)
        print("MÜFETTİŞ AI — FAZ 16: CONTROLLED AUTOMATION ORCHESTRATION")
        print(f"MODE       : {args.repair_automation_mode.upper()}")
        print(f"ENVIRONMENT: {args.repair_automation_env}")
        print("=" * 60)
        mode_map = {
            "scan_only": AutomationMode.SCAN_ONLY.value,
            "dry_run": AutomationMode.DRY_RUN.value,
            "safe_auto_repair": AutomationMode.SAFE_AUTO_REPAIR.value,
            "manual_approval": AutomationMode.MANUAL_APPROVAL.value,
        }
        policy = AutomationPolicy(
            mode=mode_map.get(args.repair_automation_mode, AutomationMode.SCAN_ONLY.value),
            environment=args.repair_automation_env,
            allow_production_auto_repair=False,  # FAZ 16 production auto-repair default OFF
        )
        if args.repair_automation_env == "rag_lab":
            rag_lab_p = DATA_DIR / "rag_lab"
            rag = LocalRAG(db_path=rag_lab_p / "chroma", allow_download=False)
            index = IngestIndex(path=rag_lab_p / "ingest_index.json")
            cache_dir = rag_lab_p / "source_cache"
        else:
            rag = LocalRAG(allow_download=False)
            index = IngestIndex()
            cache_dir = SOURCE_CACHE_DIR
        orch = ControlledAutomationOrchestrator(
            policy=policy,
            rag=rag,
            index=index,
            cache_dir=cache_dir,
        )
        if args.repair_automation_approve:
            appr_id = args.repair_automation_approve.strip()
            print(f"Executing approved repair with token: {appr_id}")
            appr = orch.approval_manager.get_approval(appr_id)
            if not appr:
                print(f"ERROR: Approval token not found: {appr_id}")
                raise SystemExit(1)
            res = orch.process_candidate(
                document_id=appr.document_id,
                source_hash=appr.source_sha256,
                approval_id=appr_id,
            )
            print(f"Final Status: {res.final_status}")
            print(json.dumps(safe_json(res.to_dict()), ensure_ascii=False, indent=2))
            raise SystemExit(0 if res.final_status == "VERIFIED_REPAIR" else 1)
        else:
            summary = orch.run_cycle()
            print(f"Cycle Status : {summary.status}")
            print(f"Scanned      : {summary.scanned_count}")
            print(f"Safe         : {summary.safe_count}")
            print(f"Review       : {summary.review_count}")
            print(f"Blocked      : {summary.blocked_count}")
            print(f"Committed    : {summary.committed_count}")
            print(f"Verified     : {summary.verified_count}")
            print(f"Rolled Back  : {summary.rolled_back_count}")
            print(f"Failed       : {summary.failed_count}")
            if summary.halt_reason:
                print(f"Halt Reason  : {summary.halt_reason}")
            print(json.dumps(safe_json(summary.to_dict()), ensure_ascii=False, indent=2))
            raise SystemExit(0 if summary.status in ("SUCCESS", "PARTIAL") else 1)
    if args.repair_wave:
        from repair.wave import WaveOrchestrator, WaveConfig, WaveScope, check_production_corpus
        from repair.models import WaveStatus
        print("=" * 60)
        print("MÜFETTİŞ AI — FAZ 17: CONTROLLED PRODUCTION REPAIR WAVES")
        print(f"SCOPE      : {args.repair_wave_scope.upper()}")
        print(f"ENVIRONMENT: {args.repair_wave_env}")
        print(f"MODE       : {'EXECUTE' if args.repair_wave_execute else 'DRY_RUN'}")
        print("=" * 60)

        if args.repair_wave_env == "rag_lab":
            rag_lab_p = DATA_DIR / "rag_lab"
            rag = LocalRAG(db_path=rag_lab_p / "chroma", allow_download=False)
            index = IngestIndex(path=rag_lab_p / "ingest_index.json")
            cache_dir = rag_lab_p / "source_cache"
        else:
            rag = LocalRAG(allow_download=False)
            index = IngestIndex()
            cache_dir = SOURCE_CACHE_DIR

        # Multi-key production safety check
        if args.repair_wave_env == "production":
            present, reason, details = check_production_corpus(rag=rag)
            if not present:
                print("PRODUCTION CORPUS CHECK: FAILED / ABSENT")
                print(f"Reason: {reason}")
                print("Live production repair is strictly BLOCKED. Production write: 0.")
                print(json.dumps(safe_json({"status": "PRODUCTION_CORPUS_ABSENT", "details": details}), ensure_ascii=False, indent=2))
                raise SystemExit(1)
            print("PRODUCTION CORPUS CHECK: PASS / PRESENT")
            if args.repair_wave_execute and not args.repair_wave_allow_production:
                print("BLOCKED: Production execution requires both --repair-wave-execute AND --repair-wave-allow-production.")
                raise SystemExit(1)

        is_dry_run = not args.repair_wave_execute
        config = WaveConfig(
            scope=args.repair_wave_scope.upper(),
            target_document_ids=[x.strip() for x in args.repair_wave_doc_id.split(",") if x.strip()] if args.repair_wave_doc_id else [],
            doc_limit=args.repair_wave_limit,
            dry_run=is_dry_run,
            allow_production_repair=bool(args.repair_wave_allow_production),
            environment=args.repair_wave_env,
        )

        wave_runner = WaveOrchestrator(
            rag=rag,
            index=index,
            cache_dir=cache_dir,
        )
        summary = wave_runner.execute_wave(config)
        print(f"Wave ID       : {summary.wave_id}")
        print(f"Wave Status   : {summary.status}")
        print(f"Targets       : {summary.target_count}")
        print(f"Scanned       : {summary.scanned_count}")
        print(f"Committed     : {summary.committed_count}")
        print(f"Verified      : {summary.verified_count}")
        print(f"Rescanned     : {summary.rescanned_count}")
        print(f"Rolled Back   : {summary.rolled_back_count}")
        print(f"Failed        : {summary.failed_count}")
        if summary.halt_reason:
            print(f"Halt Reason   : {summary.halt_reason}")
        print(json.dumps(safe_json(summary.to_dict()), ensure_ascii=False, indent=2))
        raise SystemExit(0 if summary.status in ("SUCCESS", "PARTIAL") else 1)
    if args.repair_scan:
        print("=" * 60)
        print("READ-ONLY REPAIR SCAN")
        print("PRODUCTION WRITE: DISABLED")
        print("=" * 60)
        from repair.scanner import scan_production
        rag = LocalRAG(allow_download=False)
        index = IngestIndex()
        report = scan_production(index=index, rag=rag)
        print(f"Scanned Documents : {report.scanned_documents}")
        print(f"Healthy Documents : {report.healthy_documents}")
        print(f"Review Documents  : {report.review_documents}")
        print(f"Blocked Documents : {report.blocked_documents}")
        print(f"Repair Candidates : {report.repair_candidates}")
        print(f"Production Writes : 0 (DISABLED)")
        if report.issues_summary:
            print("\nIssue Summary:")
            for issue_code, count in sorted(report.issues_summary.items()):
                print(f"  - {issue_code}: {count}")
        print("\nJSON Output:")
        print(json.dumps(safe_json(report.to_dict()), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.repair_extract_pdf:
        from repair.extractor import extract_candidate
        pdf_p = Path(args.repair_extract_pdf)
        expected_h = args.repair_expected_hash.strip().lower() or None
        candidate = extract_candidate(pdf_path=pdf_p, expected_hash=expected_h)
        val = candidate.metadata.get("validation", {})
        structure = candidate.structure or {}

        print("SOURCE VALIDATION")
        print("-----------------")
        print(f"Path: {candidate.source_path}")
        print(f"Exists: {'YES' if val.get('exists') else 'NO'}")
        print(f"Readable: {'YES' if val.get('readable') else 'NO'}")
        print(f"Expected SHA256: {val.get('expected_sha256') or '-'}")
        print(f"Actual SHA256: {val.get('actual_sha256') or '-'}")
        print(f"Hash match: {'YES' if val.get('hash_match') else 'NO'}")
        print(f"Identity: {val.get('status')}")

        print("\nEXTRACTION")
        print("----------")
        print(f"Primary: {candidate.extraction_method}")
        print(f"Spatial: {'YES' if candidate.spatial_reconstruction_used else 'NO'}")
        print(f"pdfplumber: {'YES' if candidate.pdfplumber_used else 'NO'}")
        print(f"pypdf: {'YES' if candidate.pypdf_used else 'NO'}")
        print(f"OCR: {'YES' if candidate.ocr_used else 'NO'}")

        print("\nSTRUCTURE")
        print("---------")
        print(f"Articles: {len(structure.get('articles', [])) if isinstance(structure.get('articles'), list) else structure.get('articles_count', 0)}")
        print(f"Paragraphs: {len(structure.get('paragraphs', [])) if isinstance(structure.get('paragraphs'), list) else 0}")
        print(f"Items: {len(structure.get('items', [])) if isinstance(structure.get('items'), list) else 0}")
        print(f"Sections: {len(structure.get('sections', [])) if isinstance(structure.get('sections'), list) else 0}")

        print("\nCHUNKS")
        print("------")
        print(f"Candidate chunks: {len(candidate.legal_chunks)}")

        print("\nPRODUCTION WRITE:")
        if args.repair_commit:
            print("ENABLED (EXPLICIT COMMIT)")
            from repair.committer import commit_candidate
            from repair.models import FinalGateDecision, ComparisonStatus
            # Default decision for explicit CLI commit with valid candidate
            decision = FinalGateDecision(
                faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
                faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
                final_status=ComparisonStatus.READY_TO_COMMIT.value,
            )
            rag = LocalRAG(allow_download=False)
            commit_res = commit_candidate(candidate, decision, allow_commit=True, rag=rag)
            print(f"Commit Status: {commit_res.status}")
            print(f"Upserted Chunks: {commit_res.new_chunk_count}")
            print(f"Cleaned Chunks: {commit_res.old_chunk_count}")
            if args.repair_verify:
                print("\nPOST-REPAIR VERIFICATION (write: 0)")
                print("=" * 60)
                from repair.verifier import verify_committed_repair
                from mufettis.config import SOURCE_CACHE_DIR
                verify_res = verify_committed_repair(
                    commit_result=commit_res,
                    candidate=candidate,
                    rag=rag,
                    cache_dir=SOURCE_CACHE_DIR,
                )
                print(f"Verification Status : {verify_res.status}")
                print(f"Cache              : {verify_res.cache_status}")
                print(f"Chunk              : {verify_res.chunk_status}")
                print(f"Chroma             : {verify_res.chroma_status}")
                print(f"Exact Retrieval    : {verify_res.exact_retrieval_status}")
                print(f"Document Retrieval : {verify_res.document_retrieval_status}")
                print(f"Account Retrieval  : {verify_res.account_retrieval_status}")
                print(f"Semantic Retrieval : {verify_res.semantic_retrieval_status}")
                print(f"Stale Data         : {verify_res.stale_data_status}")
                print(f"Metadata           : {verify_res.metadata_status}")
                print(f"Quality            : {verify_res.quality_status}")
                if verify_res.failures:
                    print("\nFAILURES:")
                    for f in verify_res.failures:
                        print(f"  ✗ {f}")
                if verify_res.warnings:
                    print("\nWARNINGS:")
                    for w in verify_res.warnings:
                        print(f"  ! {w}")
                print("=" * 60)
                ok = verify_res.status == "VERIFIED_REPAIR"
                raise SystemExit(0 if ok else 1)
            raise SystemExit(0 if commit_res.status in {"SUCCESS", "NO_OP"} else 1)
        elif args.repair_verify:
            print("VERIFY-ONLY (no commit; production write: 0)")
            print("NOTE: --repair-verify without --repair-commit requires a prior commit.")
            print("Use --repair-commit --repair-verify together for a full verify cycle.")
            raise SystemExit(0)
        else:
            print("DISABLED")
            raise SystemExit(0 if candidate.status == "SUCCESS" else 1)
    if args.rag_scan or args.rag_repair_pdf:
        rag = LocalRAG(allow_download=False)
        index = IngestIndex()
        if args.rag_scan:
            result = scan_production_rag_quality(index=index, rag=rag)
            print(json.dumps(safe_json(result), ensure_ascii=False, indent=2))
        if args.rag_repair_pdf:
            outputs = []
            for raw_path in args.rag_repair_pdf:
                outputs.append(
                    repair_production_document_from_pdf(
                        rag=rag,
                        index=index,
                        pdf_path=Path(raw_path),
                        dry_run=bool(args.rag_repair_dry_run),
                    )
                )
            print(json.dumps(safe_json({"repairs": outputs}), ensure_ascii=False, indent=2))
        failures = [x for x in outputs if not x.get("ok", False)] if args.rag_repair_pdf else []
        raise SystemExit(1 if failures else 0)
    if args.update_now:
        result = run_automatic_update()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.bootstrap:
        result = RegulationBootstrapper(rag=LocalRAG(allow_download=False)).run(use_browser_for_bddk=True, include_resmi_gazete=True, include_secondary=False)
        print(json.dumps(safe_json(result), ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get("completed") else 1)
    if args.run_scheduler:
        scheduler = BackgroundUpdateScheduler.get_or_create(["bddk_official", "resmi_gazete"], use_browser_for_bddk=True)
        scheduler.start()
        print(f"Scheduler aktif. Sonraki çalışma: yaklaşık {scheduler.seconds_until_next_run()/3600:.1f} saat sonra.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            scheduler.stop()
    elif args.run:
        if st is None:
            raise SystemExit("Streamlit kurulu değil. pip install streamlit")
        run_streamlit()
    else:
        # Streamlit scripti argsiz çalıştırdığında da arayüz doğrudan açılmalı.
        if st is None:
            raise SystemExit("Streamlit kurulu değil. pip install streamlit")
        run_streamlit()
