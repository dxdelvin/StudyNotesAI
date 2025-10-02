import os, uuid, traceback, time
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
import boto3
from ocr import fetch_textract_text
import json
import re

REGION = os.getenv("AWS_REGION", "eu-central-1")
RAW_BUCKET = os.getenv("RAW_BUCKET")
PDF_BUCKET = os.getenv("PDF_BUCKET")
DDB_TABLE = os.getenv("DDB_TABLE")

s3 = boto3.client("s3", region_name=REGION)
textract = boto3.client("textract", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(DDB_TABLE)

app = FastAPI(title="StudyNotesAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        # Validate file type
        allowed_types = ['application/pdf', 'image/jpeg', 'image/png', 'image/tiff']
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, 
                detail=f"File type {file.content_type} not supported. Use PDF, JPEG, PNG, or TIFF")

        doc_id = str(uuid.uuid4())
        filename = f"{doc_id}_{file.filename}"
        body = await file.read()

        if len(body) > 8 * 1024 * 1024:  # ~8MB MVP guard
            raise HTTPException(status_code=413, detail="File too large for MVP. Try <8MB.")
            
        # Log file details
        print(f"Processing file: {filename}, type: {file.content_type}, size: {len(body)} bytes")

        s3.put_object(Bucket=os.environ["RAW_BUCKET"], Key=f"raw/{filename}", Body=body, ContentType=file.content_type)
        s3.put_object(Bucket=os.environ["PDF_BUCKET"], Key=f"pdfs/{filename}", Body=body, ContentType=file.content_type)

        resp = textract.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": os.environ["RAW_BUCKET"], "Name": f"raw/{filename}"}}
        )
        job_id = resp["JobId"]

        table.put_item(Item={
            "pk": f"DOC#{doc_id}",
            "sk": "META#v0",
            "filename": file.filename,
            "status": "OCR_RUNNING",
            "job_id": job_id,
            "pdf_key": f"pdfs/{filename}",
        })
        return {"doc_id": doc_id, "message": "Uploaded. OCR started."}

    except HTTPException:
        raise
    except Exception as e:
        print("UPLOAD_ERROR:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Upload failed (see logs)")
    
    
def _score(text: str, q: str) -> float:
    """Enhanced scoring function that considers semantic relevance"""
    # Normalize text and query
    text = text.lower()
    q = q.lower()
    
    # Extract key terms from the query (words longer than 2 chars)
    terms = [t for t in re.findall(r"\w+", q) if len(t) > 2]
    
    # Basic term frequency score
    term_score = sum(text.count(t) for t in terms) / len(terms) if terms else 0
    
    # Context score: check if terms appear near each other
    context_score = 0
    if terms:
        # Find windows of text containing multiple query terms
        window_size = 100  # characters
        for i in range(len(text) - window_size):
            window = text[i:i + window_size]
            terms_in_window = sum(1 for term in terms if term in window)
            context_score = max(context_score, terms_in_window / len(terms))
    
    # Keyword boosting for common question patterns
    boost = 1.0
    question_starters = {
        'what': 1.2, 'how': 1.2, 'why': 1.2, 'when': 1.2, 'where': 1.2,
        'explain': 1.3, 'describe': 1.3, 'compare': 1.3,
        'analyze': 1.4, 'discuss': 1.4, 'evaluate': 1.4
    }
    for starter, multiplier in question_starters.items():
        if q.startswith(starter):
            boost = multiplier
            break
    
    # Combine scores with weights
    final_score = (term_score * 0.4 + context_score * 0.6) * boost
    return final_score


@app.get("/ask")
def ask(q: str):
    # Validate question
    if len(q.strip()) < 3:
        return {"answer": "Please ask a more specific question.", "sources": []}

    # Find documents that are ready
    resp = table.scan(
        FilterExpression="attribute_exists(#s) AND #s = :r",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": "READY"}
    )
    items = resp.get("Items", [])
    if not items:
        return {"answer": "No notes indexed yet.", "sources": []}

    # Get all pages from all ready documents
    all_pages = []
    for item in items:
        doc_id = item["pk"].split("#", 1)[1]
        start_key = None
        while True:
            kwargs = {
                "KeyConditionExpression": "pk = :pk",
                "ExpressionAttributeValues": {":pk": f"DOC#{doc_id}"}
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            qr = table.query(**kwargs)
            for it in qr["Items"]:
                if it["sk"].startswith("PAGE#"):
                    all_pages.append(it)
            start_key = qr.get("LastEvaluatedKey")
            if not start_key:
                break

    # Score and rank all pages
    for p in all_pages:
        p["score"] = _score(p.get("text", ""), q)
    
    # Get top scoring pages
    top = sorted(
        [p for p in all_pages if p["score"] > 0.1],  # Minimum relevance threshold
        key=lambda x: x["score"],
        reverse=True
    )[:3]
    
    if not top:
        return {"answer": "I couldn't find relevant information about that.", "sources": []}

    # Extract relevant snippets and prepare response
    snippets, sources = [], []
    for p in top:
        txt = p["text"]
        # Find the most relevant section
        best_snippet = ""
        best_score = 0
        
        # Break text into overlapping chunks
        chunk_size = 200
        overlap = 50
        for i in range(0, len(txt), chunk_size - overlap):
            chunk = txt[i:i + chunk_size]
            score = _score(chunk, q)
            if score > best_score:
                best_score = score
                best_snippet = chunk

        if best_snippet:
            # Clean up the snippet
            best_snippet = best_snippet.replace("\n", " ").strip()
            # Add context if snippet starts mid-sentence
            if not best_snippet[0].isupper() and i > 0:
                prev_period = txt.rfind(".", 0, i)
                if prev_period != -1:
                    best_snippet = txt[prev_period + 1:i] + best_snippet
            snippets.append(best_snippet)

            # Generate presigned URL for the source
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": PDF_BUCKET, "Key": p["pdf_key"]},
                ExpiresIn=3600
            ) + f"#page={p['page']}"
            sources.append({
                "page": p["page"],
                "url": url,
                "relevance": round(p["score"] * 100)  # Add relevance score
            })

    # Format the answer
    if snippets:
        answer = "Here's what I found:\n\n" + "\n\n".join(
            f"• {snippet}..." for snippet in snippets
        )
    else:
        answer = "I couldn't find specific information about that in the documents."

    return {
        "answer": answer,
        "sources": sources,
        "query": q  # Return the query for context
    }


PROCESSED_PREFIX = "processed"

@app.post("/process")
def process(doc_id: str):
    # 1) find the job_id for this doc
    r = table.get_item(Key={"pk": f"DOC#{doc_id}", "sk": "META#v0"})
    item = r.get("Item")
    if not item:
        return {"ok": False, "msg": "doc not found"}
    job_id = item["job_id"]
    pdf_key = item["pdf_key"]

    # 2) fetch pages from Textract
    pages = fetch_textract_text(job_id)

    # 3) save per-page text to S3 and DDB
    for p in pages:
        s3.put_object(
            Bucket=RAW_BUCKET,
            Key=f"{PROCESSED_PREFIX}/{doc_id}/page-{p['page']}.json",
            Body=json.dumps(p).encode("utf-8"),
            ContentType="application/json",
        )
        table.put_item(Item={
            "pk": f"DOC#{doc_id}",
            "sk": f"PAGE#{p['page']}",
            "page": p["page"],
            "pdf_key": pdf_key,
            "text": p["text"][:38000],
        })

    # 4) mark doc READY
    table.update_item(
        Key={"pk": f"DOC#{doc_id}", "sk": "META#v0"},
        UpdateExpression="SET #s = :r",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": "READY"},
    )
    return {"ok": True, "pages": len(pages)}

handler = Mangum(app)

