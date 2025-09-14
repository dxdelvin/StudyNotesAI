import os, uuid
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
import boto3

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REGION = os.getenv("AWS_REGION", "eu-central-1")
RAW_BUCKET = os.getenv("RAW_BUCKET")
PDF_BUCKET = os.getenv("PDF_BUCKET")
DDB_TABLE = os.getenv("DDB_TABLE")

s3 = boto3.client("s3", region_name=REGION)
textract = boto3.client("textract", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(DDB_TABLE)


app = FastAPI(title="StudyNotesAI")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    1) Save file into S3 'raw/' (Textract reads from here).
    2) Also copy to 'pdfs/' for later viewing via pre-signed URL.
    3) Start Textract OCR (async) and store job_id in DynamoDB.
    """
    # 1. Generate a doc id for tracking
    doc_id = str(uuid.uuid4())
    filename = f"{doc_id}_{file.filename}"

    # 2. Read file body
    body = await file.read()

    # 3. Save original to raw bucket (Textract will read this)
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"raw/{filename}",
        Body=body,
        ContentType=file.content_type
    )

    # 4. Save a copy for viewing (we'll presign this later)
    s3.put_object(
        Bucket=PDF_BUCKET,
        Key=f"pdfs/{filename}",
        Body=body,
        ContentType=file.content_type
    )

    # 5. Kick off OCR
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": RAW_BUCKET, "Name": f"raw/{filename}"}}
    )
    job_id = resp["JobId"]

    # 6. Record metadata in DynamoDB
    table.put_item(Item={
        "pk": f"DOC#{doc_id}",
        "sk": "META#v0",
        "filename": file.filename,
        "status": "OCR_RUNNING",
        "job_id": job_id,
        "pdf_key": f"pdfs/{filename}",
    })

    return {"doc_id": doc_id, "message": "Uploaded. OCR started."}

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

