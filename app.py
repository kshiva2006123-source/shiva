import os
import io
from flask import Flask, request, send_file, send_from_directory, jsonify
from PIL import Image
import fitz  # PyMuPDF
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

def compress_image(file_obj, target_mb, max_width, max_height, explicit_quality=80):
    img = Image.open(file_obj)
    
    # Resize if dimensions are provided
    if max_width or max_height:
        original_width, original_height = img.size
        ratio = min(max_width / original_width if max_width else 1.0, 
                    max_height / original_height if max_height else 1.0)
        if ratio < 1.0:
            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    format_to_save = 'JPEG'
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    def save_with_quality(img_obj, q):
        out = io.BytesIO()
        img_obj.save(out, format=format_to_save, quality=q, optimize=True)
        out.seek(0)
        return out

    # If target_mb is provided, use binary search. Otherwise, use explicit quality.
    if target_mb:
        target_bytes = target_mb * 1024 * 1024
        
        # Binary search for quality
        low = 5
        high = 95
        best_output = None
        
        while low <= high:
            mid = (low + high) // 2
            temp_output = save_with_quality(img, mid)
            size = temp_output.getbuffer().nbytes
            
            if size <= target_bytes:
                best_output = temp_output
                low = mid + 1
            else:
                high = mid - 1
                
        if best_output:
            return best_output, 'image/jpeg', 'compressed.jpg'
            
        # If even quality 5 is too big, aggressively scale down dimensions
        scale = 0.9
        current_img = img
        while scale > 0.1:
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            if new_w < 10 or new_h < 10:
                break
            current_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            temp_output = save_with_quality(current_img, 10)
            if temp_output.getbuffer().nbytes <= target_bytes:
                return temp_output, 'image/jpeg', 'compressed.jpg'
            scale -= 0.1
            
        return save_with_quality(current_img, 5), 'image/jpeg', 'compressed.jpg'
    
    # If no target size, use the provided quality value
    return save_with_quality(img, explicit_quality), 'image/jpeg', 'compressed.jpg'

def compress_pdf(file_obj, target_mb=None):
    pdf_bytes = file_obj.read()
    
    target_bytes = target_mb * 1024 * 1024 if target_mb else None
    
    def try_compress(quality, scale=1.0):
        doc = fitz.open("pdf", pdf_bytes)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    pil_img = Image.open(io.BytesIO(image_bytes))
                    if pil_img.mode in ('RGBA', 'P'):
                        pil_img = pil_img.convert('RGB')
                    
                    if scale < 1.0:
                        new_w = max(10, int(pil_img.width * scale))
                        new_h = max(10, int(pil_img.height * scale))
                        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        
                    img_io = io.BytesIO()
                    pil_img.save(img_io, format="JPEG", quality=quality, optimize=True)
                    page.replace_image(xref, stream=img_io.getvalue())
                except Exception:
                    pass
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True, clean=True)
        doc.close()
        output.seek(0)
        return output

    if target_bytes:
        # Binary search for quality
        low = 5
        high = 60
        best_output = None
        
        while low <= high:
            mid = (low + high) // 2
            temp_output = try_compress(mid)
            size = temp_output.getbuffer().nbytes
            
            if size <= target_bytes:
                best_output = temp_output
                low = mid + 1
            else:
                high = mid - 1
                
        if best_output:
            return best_output, 'application/pdf', 'compressed.pdf'
            
        # If even quality 5 is too big, scale down
        scale = 0.8
        while scale > 0.1:
            temp_output = try_compress(10, scale)
            if temp_output.getbuffer().nbytes <= target_bytes:
                return temp_output, 'application/pdf', 'compressed.pdf'
            scale -= 0.2
            
        return try_compress(5, 0.1), 'application/pdf', 'compressed.pdf'

    return try_compress(40), 'application/pdf', 'compressed.pdf'

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
    
    try:
        if ext in ['jpg', 'jpeg', 'png']:
            output, mimetype, out_filename = compress_image(file, target_mb, max_width, max_height, quality)
        elif ext == 'pdf':
            output, mimetype, out_filename = compress_pdf(file, target_mb)
        else:
            return jsonify({'error': 'Unsupported file format'}), 400
            
        return send_file(
            output,
            mimetype=mimetype,
            as_attachment=True,
            download_name=out_filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
