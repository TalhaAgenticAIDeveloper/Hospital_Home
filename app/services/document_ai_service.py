"""
Document AI Service — Groq LLM-powered medical document summarization & OCR.

Provides:
- Text extraction from digital PDFs via pypdf (with graceful fallback).
- Multimodal Vision analysis via Groq Vision API (llama-3.2-11b-vision-preview)
  for medical images (PNG, JPG) and scanned/photographed PDFs.
- Intelligent detection of blurry, low-resolution, or illegible documents without
  crashing or hallucinating.
- Database caching of generated summaries on the PatientDocument record.
"""

import base64
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.patient_document import PatientDocument
from app.repositories.patient_document_repository import PatientDocumentRepository
from app.schemas.patient_document import DocumentSummaryResponse

logger = get_logger(__name__)
settings = get_settings()

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You are an expert clinical AI assistant helping a certified doctor review patient medical history records before a telemedicine consultation.

CRITICAL INSTRUCTIONS:
1. Assess Document Quality & Legibility:
   - If the document image or text is blurry, cut off, unreadable, or of too poor quality to reliably extract medical information, DO NOT guess or hallucinate.
   - If unreadable or blurry, begin your response clearly with:
     "⚠️ [Document Unclear / Low Legibility]"
     Followed by a concise explanation (e.g. "The scan/photo is too blurry or low-resolution to extract medical metrics with certainty. Doctor manual review of the original file is recommended.")

2. If the document is readable, provide a structured, professional clinical summary:
   - 📄 Document Type: (e.g., Blood Test Report, Prescription, Ultrasound Scan, Discharge Summary)
   - 📅 Date & Provider: (if visible on document)
   - 🔬 Key Findings & Test Values: (highlight any abnormal or out-of-range values with bold indicators)
   - 💊 Prescribed Medications / Dosages: (if applicable)
   - 💡 Clinical Impression for Doctor: (2-3 concise sentences summarizing what the consulting doctor should know)

3. Format your response cleanly using Markdown bullet points and bold headers. Keep it objective, clinically relevant, and free of unnecessary conversational filler.
"""


class DocumentAIService:
    """Service for extracting text/images from patient documents and generating Groq AI summaries."""

    @staticmethod
    def _extract_pdf_text_and_images(file_path: str) -> Tuple[str, Optional[bytes]]:
        """
        Attempt to extract text from a PDF file using pypdf.
        If the PDF is scanned (little or no text), attempt to extract page images.
        """
        try:
            from pypdf import PdfReader  # Optional import (installed via requirements.txt)
        except ImportError:
            logger.warning("pypdf is not installed yet. Falling back to raw file reading.")
            return "", None

        try:
            reader = PdfReader(file_path)
            extracted_pages = []
            first_image_bytes = None

            for page_idx, page in enumerate(reader.pages):
                # Extract text
                page_text = page.extract_text() or ""
                if page_text.strip():
                    extracted_pages.append(page_text.strip())

                # Check for images if we don't have one yet
                if not first_image_bytes and hasattr(page, "images"):
                    for img in page.images:
                        first_image_bytes = img.data
                        break

            full_text = "\n\n".join(extracted_pages).strip()
            return full_text, first_image_bytes

        except Exception as exc:
            logger.warning(f"Failed to parse PDF with pypdf: {exc}")
            return "", None

    @staticmethod
    async def _call_groq_api(messages: list, model: str) -> str:
        """Call Groq OpenAI-compatible Chat Completions API using httpx."""
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError(
                "Groq API key is not configured. Please set GROQ_API in backend .env."
            )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(GROQ_CHAT_COMPLETIONS_URL, json=payload, headers=headers)

            if resp.status_code != 200:
                err_text = resp.text
                logger.error(f"Groq API error {resp.status_code}: {err_text}")
                raise ValidationError(f"Groq AI service error ({resp.status_code}): {err_text[:200]}")

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValidationError("Groq AI returned an empty response.")

            raw_content = choices[0].get("message", {}).get("content", "").strip()
            # Clean <think>...</think> tags if reasoning model (e.g. Qwen, DeepSeek)
            cleaned_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
            if "<think>" in cleaned_content and "</think>" not in cleaned_content:
                cleaned_content = cleaned_content.split("<think>", 1)[0].strip()
            return cleaned_content or raw_content

    @classmethod
    async def summarize_patient_document(
        cls,
        session: AsyncSession,
        document_id: uuid.UUID,
        meeting_id: Optional[uuid.UUID] = None,
        force_refresh: bool = False,
    ) -> DocumentSummaryResponse:
        """
        Summarize a patient document using Groq LLM.

        - If cached and force_refresh is False, returns the cached summary.
        - Digital PDF -> Extracts text and uses text LLM (settings.GROQ_MODEL).
        - Images or Scanned PDF -> Uses Groq Vision (settings.GROQ_VISION_MODEL).
        - Gracefully detects and flags blurry/unclear documents.
        """
        document = await PatientDocumentRepository.get_by_id(session, document_id)
        if not document:
            raise NotFoundError("Patient document not found")

        # Return cached summary only if not forcing refresh and previously completed successfully
        if document.ai_summary and not force_refresh and document.ai_summary_status == "completed":
            return DocumentSummaryResponse(
                document_id=document.id,
                meeting_id=meeting_id,
                label=document.label,
                original_filename=document.original_filename,
                status=document.ai_summary_status or "completed",
                summary=document.ai_summary,
                is_cached=True,
                generated_at=document.ai_summary_generated_at or document.created_at,
            )

        if not os.path.exists(document.file_path):
            raise NotFoundError("Document file not found on server")

        label_or_name = document.label or document.original_filename
        mime_type = document.mime_type.lower()
        summary_text = ""
        summary_status = "completed"

        # ── Branch A: PDF Document ───────────────────────────────────────────
        if mime_type == "application/pdf":
            extracted_text, scanned_image_bytes = cls._extract_pdf_text_and_images(document.file_path)

            if len(extracted_text) >= 80:
                # We have sufficient digital text — use text model
                model_to_use = settings.GROQ_MODEL or "llama-3.3-70b-versatile"
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Patient Document: {label_or_name}\n"
                            f"File Name: {document.original_filename}\n\n"
                            f"--- Extracted Document Text ---\n"
                            f"{extracted_text[:9000]}\n"
                            f"--- End of Document Text ---\n\n"
                            "Please provide your structured clinical summary."
                        ),
                    },
                ]
                try:
                    summary_text = await cls._call_groq_api(messages, model=model_to_use)
                except Exception as exc:
                    logger.error(f"Text summarization failed: {exc}")
                    summary_text = f"⚠️ Could not generate AI summary: {exc}"
                    summary_status = "failed"

            elif scanned_image_bytes:
                # Scanned PDF with embedded image — use scan model from .env (e.g. GROQ_SCAN_MODEL)
                vision_model = settings.groq_scan_model
                b64_image = base64.b64encode(scanned_image_bytes).decode("utf-8")
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Please analyze this scanned medical document ({label_or_name}) "
                                    f"and provide a structured clinical summary."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}"
                                },
                            },
                        ],
                    },
                ]
                try:
                    summary_text = await cls._call_groq_api(messages, model=vision_model)
                except Exception as exc:
                    logger.error(f"Vision summarization of scanned PDF failed: {exc}")
                    summary_text = f"⚠️ Could not generate AI summary from scanned PDF: {exc}"
                    summary_status = "failed"

            else:
                # PDF has neither readable text nor extractable images
                summary_text = (
                    "⚠️ [Document Unclear / No Extractable Content]\n\n"
                    "This PDF does not contain machine-readable text layers or clear scan images. "
                    "The file might be a flattened low-resolution document. "
                    "Please use the **View Document** button to inspect the original PDF directly."
                )
                summary_status = "unclear"

        # ── Branch B: Image Document (JPEG, PNG, WEBP) ───────────────────────
        elif mime_type.startswith("image/"):
            try:
                with open(document.file_path, "rb") as img_file:
                    raw_bytes = img_file.read()

                b64_img = base64.b64encode(raw_bytes).decode("utf-8")
                vision_model = settings.groq_scan_model

                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Please inspect and summarize this medical document image: {label_or_name}. "
                                    "If the image is blurry, low-resolution, or illegible, follow your instructions "
                                    "to notify the doctor clearly."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{b64_img}"
                                },
                            },
                        ],
                    },
                ]
                summary_text = await cls._call_groq_api(messages, model=vision_model)

            except Exception as exc:
                logger.error(f"Vision analysis of image failed: {exc}")
                summary_text = f"⚠️ Could not analyze document image: {exc}"
                summary_status = "failed"

        else:
            summary_text = "⚠️ Unsupported file type for AI summarization."
            summary_status = "failed"

        # ── Check if LLM flagged document as blurry / unclear ────────────────
        if (
            "⚠️ [Document Unclear" in summary_text
            or "Document Unclear" in summary_text
            or "Low Legibility" in summary_text
            or "too blurry" in summary_text.lower()
            or "illegible" in summary_text.lower()
        ):
            summary_status = "unclear"

        # ── Save to Database Cache ───────────────────────────────────────────
        now = datetime.now(timezone.utc)
        document.ai_summary = summary_text
        document.ai_summary_status = summary_status
        document.ai_summary_generated_at = now
        await session.commit()

        logger.info(
            f"AI summary generated: doc_id={document.id} status={summary_status} "
            f"length={len(summary_text)}"
        )

        return DocumentSummaryResponse(
            document_id=document.id,
            meeting_id=meeting_id,
            label=document.label,
            original_filename=document.original_filename,
            status=summary_status,
            summary=summary_text,
            is_cached=False,
            generated_at=now,
        )
