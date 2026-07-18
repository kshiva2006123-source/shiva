import os
import io
import shutil
from flask import Flask, request, send_file, send_from_directory, jsonify, after_this_request
from PIL import Image
import fitz  # PyMuPDF
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

def compress_image(input_path, target_mb, max_width, max_height, explicit_quality=80):
    original_size = os.path.getsize(input_path)
    img = Image.open(input_path)
    
    if max_width or max_height:
        original_width, original_height = img.size
        ratio = min(max_width / original_width if max_width else 1.0, 
                    max_height / original_height if max_height else 1.0)
        if ratio < 1.0:
            img = img.resize((int(original_width * ratio), int(original_height * ratio)), Image.Resampling.LANCZOS)
    
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    output_fd, output_path = tempfile.mkstemp(suffix=".jpg")
    os.close(output_fd)

    if target_mb:
        target_bytes = target_mb * 1024 * 1024
        
        # 1. Pre-check: If already smaller than target, just return it.
        if original_size <= target_bytes and not (max_width or max_height):
            os.remove(output_path)
            return input_path, 'image/jpeg', 'compressed.jpg'

        low, high = 5, 95
        best_path = None
        
        # Binary search for image quality
        while low <= high:
            mid = (low + high) // 2
            img.save(output_path, format='JPEG', quality=mid, optimize=True)
            if os.path.getsize(output_path) <= target_bytes:
                best_path = output_path
                low = mid + 1 # Try to get better quality
            else:
                high = mid - 1 # Reduce size
                
        if best_path:
            return output_path, 'image/jpeg', 'compressed.jpg'
            
        # Aggressive scaling down if still too big
        scale = 0.9
        while scale > 0.1:
            new_w, new_h = int(img.width * scale), int(img.height * scale)
            if new_w < 10 or new_h < 10: break
            current_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            current_img.save(output_path, format='JPEG', quality=10, optimize=True)
            if os.path.getsize(output_path) <= target_bytes:
                return output_path, 'image/jpeg', 'compressed.jpg'
            scale -= 0.1
            
    img.save(output_path, format='JPEG', quality=explicit_quality, optimize=True)
    
    # 2. Post-check Safety Net: Never return a file larger than the original
    if os.path.getsize(output_path) >= original_size:
        os.remove(output_path)
        return input_path, 'image/jpeg', 'compressed.jpg'
        
    return output_path, 'image/jpeg', 'compressed.jpg'

def compress_pdf(input_path, target_mb):
    original_size = os.path.getsize(input_path)
    
    # 1. Pre-check
    if target_mb and original_size <= (target_mb * 1024 * 1024):
        return input_path, 'application/pdf', 'compressed.pdf'
        
    output_fd, output_path = tempfile.mkstemp(suffix=".pdf")
    os.close(output_fd)
    
    quality_steps = [50, 35, 20, 10]
    
    for quality in quality_steps:
        doc = fitz.open(input_path)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    pil_img = Image.open(io.BytesIO(image_bytes))
                    if pil_img.mode in ('RGBA', 'P'):
                        pil_img = pil_img.convert('RGB')
                    
                    img_io = io.BytesIO()
                    pil_img.save(img_io, format="JPEG", quality=quality, optimize=True)
                    doc.replace_image(xref, stream=img_io.getvalue())
                except Exception:
                    pass
        
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        
        if not target_mb or (os.path.getsize(output_path) <= (target_mb * 1024 * 1024)):
            break
            
    # 2. Post-check Safety Net
    if os.path.getsize(output_path) >= original_size:
        os.remove(output_path)
        return input_path, 'application/pdf', 'compressed.pdf'
        
    return output_path, 'application/pdf', 'compressed.pdf'

@app.route('/compress', methods=['POST'])
def compress_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    target_mb = request.form.get('targetSizeMB', type=float)
    max_width = request.form.get('targetWidth', type=int)
    max_height = request.form.get('targetHeight', type=int)
    quality = request.form.get('quality', default=80, type=int)
    
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower()
    
    # Save to disk immediately to handle massive files safely
    input_fd, input_path = tempfile.mkstemp(suffix=f".{ext}")
    os.close(input_fd)
    file.save(input_path)
    
    output_path = None
    
    try:
        if ext in ['jpg', 'jpeg', 'png']:
            output_path, mimetype, out_filename = compress_image(input_path, target_mb, max_width, max_height, quality)
        elif ext == 'pdf':
            output_path, mimetype, out_filename = compress_pdf(input_path, target_mb)
        else:
            if os.path.exists(input_path): os.remove(input_path)
            return jsonify({'error': 'Unsupported file format'}), 400
            
        @after_this_request
        def cleanup(response):
            try:
                # Be careful not to delete the file if input_path and output_path are the same (pre-check passed)
                if os.path.exists(input_path) and input_path != output_path: 
                    os.remove(input_path)
                if output_path and os.path.exists(output_path) and output_path != input_path: 
                    os.remove(output_path)
            except Exception as e:
                print(f"Cleanup error: {e}")
            return response

        return send_file(
            output_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=out_filename
        )
    except Exception as e:
        if os.path.exists(input_path): os.remove(input_path)
        if output_path and os.path.exists(output_path): os.remove(output_path)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
