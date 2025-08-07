import os
import json
import streamlit as st
from openai import OpenAI
from supabase import create_client, Client
from io import StringIO, BytesIO
from tempfile import NamedTemporaryFile
import time
import PyPDF2
import docx2txt
from dotenv import load_dotenv
import base64
from PIL import Image
import uuid
import fitz
import zipfile
from xml.etree import ElementTree

# Client / credential set-up
load_dotenv()

def get_env_variable(var_name):
    try:
        return st.secrets[var_name]
    except Exception:
        return os.getenv(var_name)

openai_api_key = get_env_variable("OPENAI_API_KEY")
supabase_url = get_env_variable("SUPABASE_URL")
supabase_key = get_env_variable("SUPABASE_KEY")
assistant_id = get_env_variable("ASSISTANT_ID")

if not openai_api_key or not supabase_url or not supabase_key or not assistant_id:
    st.error("API keys, credentials, or assistant ID are not properly set.")
    st.stop()

client = OpenAI(api_key=openai_api_key)
supabase: Client = create_client(supabase_url, supabase_key)

st.session_state.assistant = client.beta.assistants.retrieve(assistant_id)

if "messages" not in st.session_state:
    st.session_state.messages = []

# Helper Functions for Image Processing
def create_supabase_bucket_if_not_exists():
    """Ensure the mcq-images bucket exists in Supabase Storage"""
    try:
        # Try to get bucket info
        supabase.storage.get_bucket('mcq-images')
    except:
        try:
            # Create bucket if it doesn't exist
            supabase.storage.create_bucket('mcq-images', {'public': True})
            st.info("Created mcq-images storage bucket")
        except Exception as e:
            st.warning(f"Could not create storage bucket: {e}")

def upload_image_to_supabase_storage(image_data, original_filename, question_index=0):
    """Upload image to Supabase Storage and return public URL"""
    try:
        create_supabase_bucket_if_not_exists()
        
        # Generate unique filename with question context
        file_extension = original_filename.split('.')[-1] if '.' in original_filename else 'png'
        unique_filename = f"question_{question_index}_{uuid.uuid4()}.{file_extension}"
        
        # Convert to bytes if it's a PIL Image
        if isinstance(image_data, Image.Image):
            img_byte_arr = BytesIO()
            image_data.save(img_byte_arr, format='PNG')
            image_data = img_byte_arr.getvalue()
        
        # Upload to Supabase Storage
        response = supabase.storage.from_("mcq-images").upload(
            path=unique_filename,
            file=image_data,
            file_options={"content-type": f"image/{file_extension}"}
        )
        
        # Get public URL
        public_url_response = supabase.storage.from_("mcq-images").get_public_url(unique_filename)
        public_url = public_url_response.get('publicUrl') if hasattr(public_url_response, 'get') else str(public_url_response)
        
        st.success(f"Uploaded image: {unique_filename}")
        return public_url
        
    except Exception as e:
        st.error(f"Error uploading image to Supabase Storage: {e}")
        return None

def extract_images_from_pdf_advanced(pdf_path):
    """Extract images from PDF with position information using PyMuPDF"""
    images = []
    try:
        doc = fitz.open(pdf_path)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # Get text blocks to understand content structure
            text_blocks = page.get_text("dict")
            
            # Get images on this page
            image_list = page.get_images(full=True)
            
            for img_index, img in enumerate(image_list):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    # Get image rectangle (position on page)
                    img_rect = page.get_image_rects(img)[0] if page.get_image_rects(img) else None
                    
                    # Convert to PIL Image for processing
                    pil_image = Image.open(BytesIO(image_bytes))
                    
                    image_info = {
                        'data': image_bytes,
                        'pil_image': pil_image,
                        'page': page_num + 1,
                        'index': img_index,
                        'filename': f'page_{page_num + 1}_img_{img_index}.{image_ext}',
                        'extension': image_ext,
                        'rect': img_rect,
                        'size': pil_image.size if pil_image else None
                    }
                    
                    images.append(image_info)
                    
                except Exception as img_error:
                    st.warning(f"Could not extract image {img_index} from page {page_num + 1}: {img_error}")
                    continue
        
        doc.close()
        return images
        
    except Exception as e:
        st.error(f"Error extracting images from PDF: {e}")
        return []

def extract_images_from_docx_advanced(docx_path):
    """Extract images from DOCX file with better handling"""
    images = []
    try:
        with zipfile.ZipFile(docx_path, 'r') as docx_zip:
            # Get all image files from the media folder
            image_files = [f for f in docx_zip.namelist() if f.startswith('word/media/') and any(f.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp'])]
            
            for i, img_file in enumerate(image_files):
                try:
                    img_data = docx_zip.read(img_file)
                    filename = img_file.split('/')[-1]
                    
                    # Convert to PIL Image
                    pil_image = Image.open(BytesIO(img_data))
                    
                    image_info = {
                        'data': img_data,
                        'pil_image': pil_image,
                        'index': i,
                        'filename': filename,
                        'extension': filename.split('.')[-1] if '.' in filename else 'png',
                        'size': pil_image.size
                    }
                    
                    images.append(image_info)
                    
                except Exception as img_error:
                    st.warning(f"Could not process image {img_file}: {img_error}")
                    continue
                    
        return images
        
    except Exception as e:
        st.error(f"Error extracting images from DOCX: {e}")
        return []

def match_images_to_questions(parsed_questions, extracted_images, file_name):
    """Match extracted images to parsed questions and upload to Supabase"""
    updated_questions = []
    
    for i, question in enumerate(parsed_questions):
        question_copy = question.copy()
        
        # Check if this question should have an image
        has_image = question.get('hasImage', False)
        image_position = question.get('imagePosition', i)  # Default to question index if not specified
        
        if has_image and image_position < len(extracted_images):
            try:
                # Get the corresponding image
                image_info = extracted_images[image_position]
                
                # Upload image to Supabase Storage
                image_url = upload_image_to_supabase_storage(
                    image_info['data'], 
                    image_info['filename'],
                    question_index=i
                )
                
                if image_url:
                    question_copy['image'] = image_url
                    st.success(f"Linked image to question {i + 1} from {file_name}")
                else:
                    st.warning(f"Failed to upload image for question {i + 1}")
                    
            except Exception as e:
                st.error(f"Error processing image for question {i + 1}: {e}")
        
        # Clean up processing fields
        question_copy.pop('hasImage', None)
        question_copy.pop('imagePosition', None)
        question_copy.pop('source_file', None)
        
        updated_questions.append(question_copy)
    
    return updated_questions

def process_file_with_enhanced_extraction(uploaded_file):
    """Process file and extract both text and images with better coordination"""
    file_name = uploaded_file.name
    text_content = ""
    extracted_images = []
    
    try:
        if uploaded_file.type == "application/pdf":
            # For PDFs
            with NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                temp_pdf.write(uploaded_file.read())
                temp_pdf.flush()
                
                # Extract text using PyPDF2
                reader = PyPDF2.PdfReader(temp_pdf.name)
                for page in reader.pages:
                    text_content += page.extract_text() + "\n"
                
                # Extract images using PyMuPDF (more advanced)
                extracted_images = extract_images_from_pdf_advanced(temp_pdf.name)
                
                os.unlink(temp_pdf.name)  # Clean up temp file
                
        elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            # For DOCX files
            with NamedTemporaryFile(delete=False, suffix=".docx") as temp_docx:
                temp_docx.write(uploaded_file.read())
                temp_docx.flush()
                
                # Extract text
                text_content = docx2txt.process(temp_docx.name)
                
                # Extract images
                extracted_images = extract_images_from_docx_advanced(temp_docx.name)
                
                os.unlink(temp_docx.name)  # Clean up temp file
                
        elif uploaded_file.type == "text/plain":
            # For TXT files (no images)
            stringio = StringIO(uploaded_file.getvalue().decode("utf-8"))
            text_content = stringio.read()
            
        else:
            st.error(f"Unsupported file type: {uploaded_file.type}")
            return None, None
            
    except Exception as e:
        st.error(f"Error processing {file_name}: {e}")
        return None, None
    
    return text_content, extracted_images

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("What is your question?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Create a new thread for each conversation
    thread = client.beta.threads.create()

    # Add user message to the thread
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=prompt
    )

    # Run the assistant
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=st.session_state.assistant.id,
    )

    # Wait for the run to complete
    while run.status != "completed":
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    # Retrieve and display the assistant's response
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    assistant_message = messages.data[0].content[0].text.value

    st.session_state.messages.append({"role": "assistant", "content": assistant_message})
    with st.chat_message("assistant"):
        st.markdown(assistant_message)

# Main File Processing Section
st.title("📚 MCQ Parser with Automatic Image Extraction")

st.write("""
This app processes PDF, DOCX, or TXT files containing MCQ questions with embedded images.
It automatically:
1. Extracts text content and parses questions
2. Extracts embedded images from the files
3. Matches images to their corresponding questions
4. Uploads images to Supabase Storage and stores URLs in the database
""")

uploaded_files = st.file_uploader(
    "Upload PDF, DOCX, or TXT files with embedded images", 
    type=["pdf", "docx", "txt"], 
    accept_multiple_files=True
)

if uploaded_files:
    data_list = []
    any_errors = False

    for uploaded_file in uploaded_files:
        file_name = uploaded_file.name
        st.write(f"🔄 Processing **{file_name}**...")

        # Process file and extract text and images
        text_content, extracted_images = process_file_with_enhanced_extraction(uploaded_file)
        
        if text_content is None:
            any_errors = True
            continue
        
        if extracted_images:
            st.success(f"Extracted {len(extracted_images)} images from **{file_name}**")
            
            # Display extracted images in an expandable section
            with st.expander(f"Preview images from {file_name}"):
                cols = st.columns(min(3, len(extracted_images)))
                for idx, img in enumerate(extracted_images):
                    with cols[idx % 3]:
                        if img.get('pil_image'):
                            st.image(img['pil_image'], caption=f"Image {idx + 1}: {img['filename']}", width=200)
                        st.caption(f"Size: {img.get('size', 'Unknown')}")
        else:
            st.info(f"ℹ️ No images found in **{file_name}**")

        # Use OpenAI API to parse the content with enhanced image awareness
        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                # Create enhanced prompt that's aware of extracted images
                image_context = f"\n\nIMPORTANT: This document contains {len(extracted_images)} extracted images. " if extracted_images else "\n\nNote: No images were found in this document. "
                
                prompt = f"""
                You will be provided data from the text. You must output them in a JSON format with the following keys:
                
                - questionStem
                - correctAnswerId  
                - answersArray (as a list)
                - explanationList (as a list)
                - conditionName
                - moduleId
                - leadQuestion
                - presentationId
                - presentationId2
                - hasImage (boolean - true if this question has an associated image)
                - imagePosition (integer - if hasImage is true, indicate which image corresponds to this question, starting from 0)
                
                {image_context}
                When parsing questions, look for any references to images, figures, diagrams, or visual elements.
                If you detect that a question refers to or requires an image (like "based on the image above", "the figure shows", "refer to the diagram", etc.), set hasImage to true.
                For imagePosition, use the order in which images appear in the document (0 for first image, 1 for second, etc.).
                
                You will categorise each question via the module they come under (the ID number for each question will be provided for you)
                You will categorise the article via presentation in which the number will already be provided to you.
                You will also provide the medical condition name that the question is related to in the conditionName field.
                
                Example output format:
                {{
                  "0": {{
                    "questionStem": "A 70-year-old man with a history of hypertension presents with sudden onset of severe chest pain radiating to the back.",
                    "correctAnswerId": "0",
                    "answersArray": [
                      "A. Aortic dissection",
                      "B. Myocardial infarction", 
                      "C. Pulmonary embolism",
                      "D. Pericarditis",
                      "E. Pneumothorax"
                    ],
                    "explanationList": [
                      "A. Aortic dissection - Correct Answer: Aortic dissection is most likely given the description of severe chest pain radiating to the back, a hallmark of this condition.",
                      "B. Myocardial infarction - Incorrect Answer: Myocardial infarction can also cause chest pain, but it typically radiates to the arm or jaw rather than the back.",
                      "C. Pulmonary embolism - Incorrect Answer: Pulmonary embolism may cause chest pain but is usually associated with shortness of breath.",
                      "D. Pericarditis - Incorrect Answer: Pericarditis often causes sharp chest pain that worsens with inspiration, but does not typically radiate to the back.",
                      "E. Pneumothorax - Incorrect Answer: Pneumothorax can cause chest pain, but it typically presents with sudden shortness of breath and is less likely to radiate to the back."
                    ],
                    "conditionName": 7,
                    "moduleId": 1,
                    "presentationId": 41,
                    "presentationId2": 23,
                    "leadQuestion": "What is the most likely diagnosis?",
                    "hasImage": true,
                    "imagePosition": 0
                  }}
                }}
                
                CRITICAL INSTRUCTIONS:
                - YOU MUST ENSURE THAT THE LEAD-IN QUESTION IS ALWAYS SEPARATED FROM THE QUESTION STEM
                - YOU MUST PARSE ALL questions in the text, not just the first one
                - Ignore any "why the question is difficult" sections
                - INCLUDE ALL EXPLANATION DETAILS - never summarize
                - RETAIN EVERY WORD AND PARAGRAPH from the document
                - Make sure moduleId, conditionName, presentationId, presentationId2 are always an interger
                - Pay attention to any image references in the text and set hasImage/imagePosition accordingly
                - IF THERE IS NO INFORMATION REGARDING PRESENTATION ID, MAKE THIS NULL (not 0) it must be NULL
                
                Text to parse:
                {text_content}
                """

                response = client.chat.completions.create(
                    model="gpt-4.1", 
                    messages=[
                        {"role": "system", "content": "You are a precise JSON parser that extracts MCQ data while preserving all content and identifying image associations."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    max_tokens=None 
                )
                
                # Parse the JSON output from OpenAI
                json_response = response.choices[0].message.content.strip()
                json_response = json_response.replace("```json", "").replace("```", "").strip()
                
                # Parse JSON and prepare for image matching
                parsed_data = json.loads(json_response)
                file_questions = []
                
                if isinstance(parsed_data, dict):
                    for key, question_set in parsed_data.items():
                        question_set['source_file'] = file_name
                        file_questions.append(question_set)
                else:
                    parsed_data['source_file'] = file_name
                    file_questions.append(parsed_data)
                
                # Match images to questions and upload them
                if extracted_images:
                    st.info(f"🔗 Matching {len(extracted_images)} images to {len(file_questions)} questions...")
                    final_questions = match_images_to_questions(file_questions, extracted_images, file_name)
                else:
                    # No images to process, just clean up fields
                    final_questions = []
                    for q in file_questions:
                        q.pop('hasImage', None)
                        q.pop('imagePosition', None) 
                        q.pop('source_file', None)
                        final_questions.append(q)
                
                data_list.extend(final_questions)
                st.success(f"Successfully processed **{file_name}** with {len(final_questions)} questions")
                break
            
            except json.JSONDecodeError as json_error:
                if attempt < max_retries - 1:
                    st.warning(f"JSON parsing error for {file_name}. Retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    st.error(f"Failed to parse JSON for {file_name} after {max_retries} attempts")
                    st.error(f"JSON Error: {json_error}")
                    with st.expander("View raw response"):
                        st.text(json_response)
                    any_errors = True
            
            except Exception as e:
                st.error(f"Error processing {file_name}: {e}")
                any_errors = True
                break

    # Display results and upload option
    if data_list:
        st.write("### 📊 Parsed Data Preview:")
        
        # Show summary
        total_questions = len(data_list)
        questions_with_images = len([q for q in data_list if q.get('image')])
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Questions", total_questions)
        with col2:
            st.metric("Questions with Images", questions_with_images)
        with col3:
            st.metric("Success Rate", f"{questions_with_images}/{total_questions}")
        
        # Show expandable JSON preview
        with st.expander("📋 View Full JSON Data"):
            st.json(data_list)

        # Upload confirmation
        if st.button("🚀 Upload All Data to Supabase", type="primary"):
            st.write("📤 Uploading questions and images to Supabase...")
            
            progress_bar = st.progress(0)
            upload_errors = False
            
            for i, record in enumerate(data_list):
                try:
                    response = supabase.table("mcqQuestions").upsert(
                        record, 
                        on_conflict="questionStem"
                    ).execute()
                    
                    progress_bar.progress((i + 1) / len(data_list))
                    
                    if hasattr(response, 'error') and response.error:
                        st.error(f"Database error for question {i + 1}: {response.error}")
                        upload_errors = True
                    
                except Exception as e:
                    st.error(f"Upload exception for question {i + 1}: {e}")
                    upload_errors = True
            
            progress_bar.empty()
            
            if not upload_errors:
                st.success("All data uploaded successfully!")
                st.balloons()
            else:
                st.warning("⚠Some uploads failed. Check the error messages above.")
                
    else:
        if any_errors:
            st.error("No data to upload due to processing errors.")
        else:
            st.warning("No questions were extracted from the uploaded files.")
            
else:
    st.info("Please upload PDF, DOCX, or TXT files to get started.")
