# -*- coding: utf-8 -*-
"""vulture izin listesi: kullanılmıyor gibi görünen ama kullanılan adlar (tests/test_dead_code.py).

Buraya yalnızca depo genelinde aranıp gerçekten kullanıldığı ya da bilinçli tutulduğu doğrulanan ad eklenir;
her grubun gerekçesi yanında. Test dosyalarındaki bulgular denetlenmez (sahte nesnelerin imzaları vb.).
Bu dosya çalıştırılmaz; vulture yalnızca içindeki adları "kullanılmış" sayar.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

# pytest kancaları (conftest.py) — pytest adıyla çağırır.
pytest_report_header
pytest_sessionstart
pytest_sessionfinish
exitstatus
_.exitstatus

# pydantic: model ayarı ve doğrulayıcılar (dekoratörle kaydedilir), şema alanları (mufettis/analysis/models.py).
model_config
_.validate_type
_.validate_analysis_type
_.validate_plan_type
_.validate_non_empty_queries
involved_entities
target_domains
output_schema

# dataclass / enum alanları: kaydedilen ve raporlanan veri şeması (repair/models.py, gold_set.py, corpus/model.py).
SOURCE_PATH_MISSING
# Belge vakası şeması (analysis/document_benchmark.py): cevap anahtarından saklanır; imza / onay M5'te,
# kusurun mevzuat atfı M9'da ölçülür.
signatures
regulation
structure_status
plan_version
extraction_chain
normalization_enabled
structure_enabled
chunking_enabled
expected_quality_metrics
fallback_used
ocr_details
very_long_token_count
item_count
subitem_count
accounting_code_count
current_score
candidate_score
structure_before
structure_after
chunks_before
chunks_after
benchmark_id
query_type
current_rank
candidate_rank
current_document_match
candidate_document_match
current_account_match
candidate_account_match
current_reference_match
candidate_reference_match
current_metadata_match
candidate_metadata_match
current_retrievable
candidate_retrievable
target_collection
retrieval_status
restored_state
REPAIR_READY_TO_COMMIT
AUTOMATION_DECISION_MADE
AUTOMATION_APPROVAL_CONSUMED
app_version
audit_schema_version
chunk_count_before
chunk_count_after
DISABLED
approval_expiration_seconds
reason_codes
consumed_at
quality_comparison
retrieval_comparison
attempted_count
RUNNING
COMPLETED
_.consumed_at
_.quality_comparison
_.retrieval_comparison
_.attempted_count
expected_subject
expected_analysis_type
doc_type_expected
doc_type_match
unsupported_count
canonical_schema

# python-docx biçim nitelikleri: atanarak belgeye yazılır (mufettis/exports/).
_.top_margin
_.bottom_margin
_.left_margin
_.right_margin
_.alignment
_.bold
_.italic
_.style

# Demet açmada kullanılmayan öğeler (değer diğer öğeler için açılıyor).
badge_desc  # mufettis/ui/analysis_view.py
appr_rec  # repair/orchestrator.py, repair/wave.py
c_id  # repair/scanner.py

# Chroma istemcisiyle aynı imza (store.py SqliteVectorClient.get_or_create_collection).
embedding_function

# Dizge içinden alt süreçte çağrılır (mufettis/tests/runner.py: "from mufettis.tests.runner import _main").
_main

# Depo tanılama API'si: sıkıştırma (839d80f0) sonrası bilinçli korunan genel yöntem (mufettis/rag/store.py).
_.vector_rows

# ONNX Runtime oturum ayarları: atanarak kullanılır (scripts/onnx_models/).
_.intra_op_num_threads
_.inter_op_num_threads
_.graph_optimization_level
# torch.nn.Module.forward: torch çağırır (dışa aktarma sarmalayıcıları, scripts/onnx_models/export.py).
_.forward
# SentenceTransformer.encode / CrossEncoder.predict ile aynı imza (ONNX yerine geçenler, scripts/onnx_models/).
show_progress_bar
