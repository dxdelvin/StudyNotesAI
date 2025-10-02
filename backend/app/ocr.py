import boto3, os, time

REGION = os.getenv("AWS_REGION", "eu-central-1")
textract = boto3.client("textract", region_name=REGION)

def fetch_textract_text(job_id: str):
    """Return list of {page, text} from an async Textract job."""
    print(f"Starting Textract extraction for job {job_id}")
    
    # Wait for completion with detailed status
    max_attempts = 60  # 5 minutes maximum wait
    attempt = 0
    while attempt < max_attempts:
        response = textract.get_document_text_detection(JobId=job_id)
        status = response['JobStatus']
        print(f"Job status (attempt {attempt+1}): {status}")
        
        if status == 'SUCCEEDED':
            break
        elif status == 'FAILED':
            error = response.get('StatusMessage', 'Unknown error')
            print(f"Textract job failed: {error}")
            raise Exception(f"Textract failed: {error}")
        elif status == 'PARTIAL_SUCCESS':
            print("Warning: Textract completed with partial success")
            break
            
        attempt += 1
        time.sleep(5)
    
    if attempt >= max_attempts:
        raise Exception("Timeout waiting for Textract")
    
    # Rest of your existing code...
    pages = []
    next_token = None
    while True:
        if next_token:
            resp = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        else:
            resp = textract.get_document_text_detection(JobId=job_id)
        
        # Group by page with confidence scores
        page_map = {}
        for block in resp.get('Blocks', []):
            if block['BlockType'] == 'LINE':
                page_num = block.get('Page', 1)
                confidence = block.get('Confidence', 0)
                text = block.get('Text', '')
                
                if not page_map.get(page_num):
                    page_map[page_num] = []
                    
                page_map[page_num].append({
                    'text': text,
                    'confidence': confidence
                })
        
        # Format page content
        for page_num, lines in sorted(page_map.items()):
            # Filter low confidence lines
            good_lines = [line['text'] for line in lines if line['confidence'] > 50]
            
            if good_lines:  # Only add pages with content
                page_text = '\n'.join(good_lines)
                print(f"\nPage {page_num} content:")
                print("-------------------")
                print(page_text)
                print("-------------------")
                
                pages.append({
                    'page': page_num,
                    'text': page_text,
                    'confidence': sum(line['confidence'] for line in lines) / len(lines)
                })
        
        next_token = resp.get("NextToken")
        if not next_token:
            break
            
    print(f"Extracted {len(pages)} pages of text")
    return pages