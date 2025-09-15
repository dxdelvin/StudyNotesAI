import os, uuid
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
import boto3


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
        doc_id = str(uuid.uuid4())
        filename = f"{doc_id}_{file.filename}"
        body = await file.read()

        if len(body) > 8 * 1024 * 1024:  # ~8MB MVP guard
            raise HTTPException(status_code=413, detail="File too large for MVP. Try <8MB.")

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
    
@app.get("/ask")
def ask(q: str = Query(..., min_length=3)):
    """
    MVP placeholder:
    Return a stub answer + link to last uploaded PDF (page=1).
    We'll wire real search once OCR results are processed.
    """
    # grab any META item to demo a source link
    resp = table.scan(Limit=1)
    items = resp.get("Items", [])
    if not items:
        return {"answer": "No notes indexed yet.", "sources": []}

    pdf_key = items[0]["pdf_key"]
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": PDF_BUCKET, "Key": pdf_key},
        ExpiresIn=3600
    ) + "#page=1"

    return {
        "answer": f"MVP answer to: '{q}'. (Search coming next.)",
        "sources": [{"page": 1, "url": url}]
    }


handler = Mangum(app)

