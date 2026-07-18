import os
import io
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
        if original_size <= target_bytes and not (max_width or max_height):
            os.remove(output_path)
            return input_path, 'image/jpeg', 'compressed.jpg'

        low, high = 5, 95
        best_path = None
        
        while low <= high:
            mid = (low + high) // 2
            img.save(output_path, format='JPEG', quality=mid, optimize=True)
            if os.path.getsize(output_path) <= target_bytes:
                best_path = output_path
                low = mid + 1
            else:
                high = mid - 1
                
        if best_path:
            return output_path, 'image/jpeg', 'compressed.jpg'
            
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
    
    if os.path.getsize(output_path) >= original_size:
        os.remove(output_path)
        return input_path, 'image/jpeg', 'compressed.jpg'
        
    return output_path, 'image/jpeg', 'compressed.jpg'

def compress_pdf(input_path, target_mb):
    original_size = os.path.getsize(input_path)
    
    # Pre-check: No need to touch it if it's already under target
    if target_mb and original_size <= (target_mb * 1024 * 1024):
        return input_path, 'application/pdf', 'compressed.pdf'
        
    output_fd, output_path = tempfile.mkstemp(suffix=".pdf")
    os.close(output_fd)
    
    # Blazing-fast native PyMuPDF EZ compression options
    # We step down aggressive levels until we clear the goal posts or hit the maximum reduction floor.
    doc = fitz.open(input_path)
    
    # Step 1: Linearization and structural cleanup (Safe, quick drop)
    doc.save(output_path, garbage=4, deflate=True, clean=True)
    
    # Step 2: If we have a strict target and standard compression didn't reach it, use EZ optimization
    if target_mb and os.path.getsize(output_path) > (target_mb * 1024 * 1024):
        doc.close()
        
        # re-open to clear save structures
        doc = fitz.open(input_path)
        
        # Native PyMuPDF EZ compression levels
        # 1 = Low compression, 2 = Medium, 3 = High, 4 = Extreme
        for effort in [2, 3, 4]:
            try:
                doc.ez_save(output_path, garbage=4, deflate=True, clean=True, effort=effort)
                if os.path.getsize(output_path) <= (target_mb * 1024 * 1024):
                    break
            except Exception:
                pass
                
    doc.close()
    
    # Post-check Safety Net: If the compressed file ended up larger, fallback to original
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
