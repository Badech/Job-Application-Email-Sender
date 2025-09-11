#!/usr/bin/env python3
"""
Job Application Email Sender - Flask Backend
Production-ready version with improved error handling and security
"""

from flask import Flask, request, jsonify, Response, stream_with_context, render_template, send_from_directory
from flask_cors import CORS
import json
import smtplib
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from werkzeug.utils import secure_filename
import time
from typing import Generator, Dict, Any, List
from datetime import datetime
import logging

# Initialize Flask app
app = Flask(__name__)

# Configure CORS - allow all origins in development, restrict in production
if os.environ.get('ENVIRONMENT') == 'production':
    CORS(app, origins=os.environ.get('ALLOWED_ORIGINS', '*').split(','))
else:
    CORS(app, origins='*')

# Configuration


class Config:
    ALLOWED_EXTENSIONS = {'pdf'}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_RECIPIENTS = 100
    MAX_EMAIL_BODY_LENGTH = 5000
    MAX_SUBJECT_LENGTH = 200
    EMAIL_DELAY = 1  # seconds between emails to avoid rate limiting
    SMTP_TIMEOUT = 30


app.config['MAX_CONTENT_LENGTH'] = Config.MAX_FILE_SIZE

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = app.logger

# Email validation regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


class EmailValidator:
    """Handles email validation"""

    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        if not email or len(email) > 254:
            return False
        return EMAIL_REGEX.match(email.strip()) is not None

    @staticmethod
    def validate_file(filename: str) -> bool:
        """Check if file extension is allowed"""
        if not filename:
            return False
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

    @staticmethod
    def validate_pdf_content(file_data: bytes) -> bool:
        """Validate PDF file by checking magic bytes"""
        if not file_data or len(file_data) < 4:
            return False
        return file_data[:4] == b'%PDF'


class EmailSender:
    """Handles email sending operations"""

    def __init__(self, smtp_server: str = "smtp.gmail.com", smtp_port: int = 587):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port

    def create_message(self, sender_email: str, sender_name: str,
                       recipient_email: str, subject: str, body: str,
                       cv_data: bytes, cv_filename: str) -> MIMEMultipart:
        """Create email message with attachment"""
        msg = MIMEMultipart()

        # Set headers
        if sender_name and sender_name.strip():
            msg['From'] = f'"{sender_name.strip()}" <{sender_email}>'
        else:
            msg['From'] = sender_email

        msg['To'] = recipient_email
        msg['Subject'] = subject[:Config.MAX_SUBJECT_LENGTH]

        # Attach body
        msg.attach(
            MIMEText(body[:Config.MAX_EMAIL_BODY_LENGTH], 'plain', 'utf-8'))

        # Attach PDF
        if cv_data and cv_filename:
            try:
                part = MIMEBase('application', 'pdf')
                part.set_payload(cv_data)
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{secure_filename(cv_filename)}"'
                )
                msg.attach(part)
            except Exception as e:
                logger.error(f"Error attaching file: {e}")
                raise

        return msg

    def send_emails_batch(self, sender_email: str, sender_name: str,
                          password: str, subject: str, body: str,
                          recipients: List[str], cv_data: bytes,
                          cv_filename: str) -> Generator[str, None, None]:
        """Generator for sending emails with real-time updates"""

        total = len(recipients)
        sent = 0
        failed = 0
        server = None

        # Initial status
        yield self._create_response('log', 'Starting email campaign...', 'info')

        try:
            # Connect to SMTP server
            yield self._create_response('log', f'Connecting to {self.smtp_server}...', 'info')

            server = smtplib.SMTP(self.smtp_server, self.smtp_port,
                                  timeout=Config.SMTP_TIMEOUT)
            server.starttls()

            # Authenticate
            yield self._create_response('log', 'Authenticating...', 'info')

            try:
                server.login(sender_email, password)
                yield self._create_response('log', 'Authentication successful', 'success')
            except smtplib.SMTPAuthenticationError as e:
                error_msg = str(e)
                if 'Username and Password not accepted' in error_msg:
                    yield self._create_response('log',
                                                'Authentication failed! For Gmail, use an App Password, not your regular password. ' +
                                                'Generate one at: https://myaccount.google.com/apppasswords',
                                                'error')
                else:
                    yield self._create_response('log', f'Authentication failed: {error_msg}', 'error')
                return

            # Send emails
            yield self._create_response('log', f'Sending to {total} recipients...', 'info')

            for i, recipient in enumerate(recipients, 1):
                try:
                    # Validate recipient
                    if not EmailValidator.validate_email(recipient):
                        failed += 1
                        yield self._create_response('log', f'Invalid email: {recipient}', 'error')
                        continue

                    # Create and send message
                    msg = self.create_message(
                        sender_email, sender_name, recipient,
                        subject, body, cv_data, cv_filename
                    )

                    server.sendmail(sender_email, recipient, msg.as_string())
                    sent += 1

                    yield self._create_response('log', f'✓ Sent to: {recipient}', 'success')

                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to send to {recipient}: {e}")
                    yield self._create_response('log',
                                                f'✗ Failed to send to {recipient}: {str(e)}', 'error')

                # Progress update
                progress = int((i / total) * 100)
                pending = total - sent - failed

                yield json.dumps({
                    'type': 'progress',
                    'progress': progress,
                    'total': total,
                    'sent': sent,
                    'failed': failed,
                    'pending': pending
                }) + '\n'

                # Rate limiting
                if i < total:  # Don't delay after last email
                    time.sleep(Config.EMAIL_DELAY)

            # Completion
            yield self._create_response('log',
                                        f'Campaign completed! Sent: {sent}, Failed: {failed}', 'success')
            yield json.dumps({'type': 'complete', 'sent': sent, 'failed': failed}) + '\n'

        except Exception as e:
            logger.error(f"Unexpected error in send_emails_batch: {e}")
            yield self._create_response('log', f'Error: {str(e)}', 'error')
        finally:
            if server:
                try:
                    server.quit()
                except:
                    pass

    @staticmethod
    def _create_response(msg_type: str, message: str, level: str = 'info') -> str:
        """Create JSON response string"""
        return json.dumps({
            'type': msg_type,
            'message': message,
            'level': level
        }) + '\n'


# Initialize email sender
email_sender = EmailSender()


# Routes
@app.route('/')
def index():
    """Serve the main application page"""
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Error serving index: {e}")
        return jsonify({'error': 'Template not found. Make sure index.html is in templates folder'}), 500


@app.route('/favicon.ico')
def favicon():
    """Handle favicon requests to prevent 404 errors"""
    return '', 204


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        'status': 'healthy',
        'version': '2.0',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/test-email', methods=['POST'])
def test_email():
    """Test email credentials without sending"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        sender_email = data.get('sender_email', '').strip()
        password = data.get('password', '')

        # Validate email
        if not EmailValidator.validate_email(sender_email):
            return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400

        # Test connection
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(sender_email, password)
        server.quit()

        return jsonify({'status': 'success', 'message': 'Authentication successful'}), 200

    except smtplib.SMTPAuthenticationError:
        return jsonify({
            'status': 'error',
            'message': 'Authentication failed. For Gmail, use an App Password from https://myaccount.google.com/apppasswords'
        }), 401
    except Exception as e:
        logger.error(f"Test email error: {e}")
        return jsonify({'status': 'error', 'message': 'Connection failed'}), 500


@app.route('/send-emails', methods=['POST', 'OPTIONS'])
def send_emails():
    """Main endpoint for sending bulk emails"""

    # Handle preflight
    if request.method == 'OPTIONS':
        return '', 204

    try:
        # Extract form data
        sender_email = request.form.get('sender_email', '').strip()
        sender_name = request.form.get('sender_name', '').strip()
        password = request.form.get('password', '')
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()

        # Validate basic fields
        if not all([sender_email, password, subject, body]):
            return jsonify({'error': 'Missing required fields'}), 400

        # Validate sender email
        if not EmailValidator.validate_email(sender_email):
            return jsonify({'error': 'Invalid sender email format'}), 400

        # Parse and validate recipients
        try:
            recipients_raw = request.form.get('recipients', '[]')
            recipients = json.loads(recipients_raw)

            if not isinstance(recipients, list):
                return jsonify({'error': 'Recipients must be a list'}), 400

            # Filter and validate recipients
            valid_recipients = []
            for r in recipients:
                if isinstance(r, str) and EmailValidator.validate_email(r):
                    valid_recipients.append(r.strip())

            if not valid_recipients:
                return jsonify({'error': 'No valid recipients found'}), 400

            if len(valid_recipients) > Config.MAX_RECIPIENTS:
                return jsonify({'error': f'Too many recipients. Maximum: {Config.MAX_RECIPIENTS}'}), 400

        except json.JSONDecodeError:
            return jsonify({'error': 'Invalid recipients format'}), 400

        # Handle file upload
        if 'cv' not in request.files:
            return jsonify({'error': 'No CV file uploaded'}), 400

        cv_file = request.files['cv']

        if not cv_file or cv_file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        if not EmailValidator.validate_file(cv_file.filename):
            return jsonify({'error': 'Invalid file type. Only PDF files are allowed'}), 400

        # Read and validate file content
        cv_file.seek(0)
        cv_data = cv_file.read()

        if not cv_data:
            return jsonify({'error': 'Empty file uploaded'}), 400

        if len(cv_data) > Config.MAX_FILE_SIZE:
            return jsonify({'error': f'File too large. Maximum size: {Config.MAX_FILE_SIZE/1024/1024}MB'}), 400

        if not EmailValidator.validate_pdf_content(cv_data):
            return jsonify({'error': 'Invalid PDF file content'}), 400

        cv_filename = secure_filename(cv_file.filename)

        # Log the request
        logger.info(
            f"Starting email campaign: {len(valid_recipients)} recipients, from: {sender_email}")

        # Return streaming response
        return Response(
            stream_with_context(
                email_sender.send_emails_batch(
                    sender_email, sender_name, password, subject, body,
                    valid_recipients, cv_data, cv_filename
                )
            ),
            mimetype='application/x-ndjson',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )

    except Exception as e:
        logger.error(f"Error in send_emails: {str(e)}")
        return jsonify({'error': 'An error occurred processing your request'}), 500


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error"""
    return jsonify({'error': f'File too large. Maximum size: {Config.MAX_FILE_SIZE/1024/1024}MB'}), 413


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    # Get configuration from environment
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('ENVIRONMENT') != 'production'

    print("=" * 60)
    print("     Job Application Email Sender - Backend v2.0")
    print("=" * 60)
    print(f"  Server URL: http://localhost:{port}")
    print(f"  Debug Mode: {debug_mode}")
    print(f"  Max Recipients: {Config.MAX_RECIPIENTS}")
    print(f"  Max File Size: {Config.MAX_FILE_SIZE/1024/1024}MB")
    print("=" * 60)
    print("  For Gmail: Use App Password (not regular password)")
    print("  Generate at: https://myaccount.google.com/apppasswords")
    print("=" * 60)

    # Run the application
    app.run(
        debug=debug_mode,
        port=port,
        host='0.0.0.0'
    )
