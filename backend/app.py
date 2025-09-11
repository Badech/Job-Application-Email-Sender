#!/usr/bin/env python3
"""
Flask Backend for Job Application Email Sender
Provides API endpoints for the frontend to interact with the email sending functionality
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import json
import smtplib
import os
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from werkzeug.utils import secure_filename
import time
from typing import Generator
import base64
import io

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
ALLOWED_EXTENSIONS = {'pdf'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_email(email: str) -> bool:
    """Basic email validation"""
    return '@' in email and '.' in email.split('@')[1]


def create_message_with_attachment(sender_email: str, sender_name: str, recipient_email: str, subject: str,
                                   body: str, cv_data: bytes, cv_filename: str) -> MIMEMultipart:
    """Create email message with attachment using file data in memory"""
    msg = MIMEMultipart()

    # Format the From field with display name
    if sender_name:
        msg['From'] = f'"{sender_name}" <{sender_email}>'
    else:
        msg['From'] = sender_email

    msg['To'] = recipient_email
    msg['Subject'] = subject

    # Attach body
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Attach PDF from memory
    try:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(cv_data)
        encoders.encode_base64(part)

        part.add_header(
            'Content-Disposition',
            f'attachment; filename="{cv_filename}"'
        )
        msg.attach(part)
    except Exception as e:
        raise Exception(f"Error attaching file: {e}")

    return msg


def send_emails_generator(sender_email: str, sender_name: str, password: str, subject: str, body: str,
                          recipients: list, cv_data: bytes, cv_filename: str) -> Generator:
    """Generator function for sending emails with real-time updates"""

    # Gmail SMTP configuration
    smtp_server = "smtp.gmail.com"
    smtp_port = 587

    total = len(recipients)
    sent = 0
    failed = 0

    # Send initial status
    yield json.dumps({
        'type': 'log',
        'message': 'Starting email campaign...',
        'level': 'info'
    }) + '\n'

    yield json.dumps({
        'type': 'log',
        'message': f'Connecting to SMTP server ({smtp_server})...',
        'level': 'info'
    }) + '\n'

    try:
        # Connect to SMTP server
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()

        yield json.dumps({
            'type': 'log',
            'message': 'Authenticating...',
            'level': 'info'
        }) + '\n'

        # Login
        try:
            server.login(sender_email, password)
            yield json.dumps({
                'type': 'log',
                'message': 'Successfully authenticated',
                'level': 'success'
            }) + '\n'
        except Exception as auth_error:
            yield json.dumps({
                'type': 'log',
                'message': f'Authentication failed: {str(auth_error)}',
                'level': 'error'
            }) + '\n'
            raise

        yield json.dumps({
            'type': 'log',
            'message': f'Starting to send emails to {total} recipients',
            'level': 'info'
        }) + '\n'

        # Send emails
        for i, recipient in enumerate(recipients, 1):
            try:
                if not validate_email(recipient):
                    failed += 1
                    yield json.dumps({
                        'type': 'log',
                        'message': f'Invalid email: {recipient}',
                        'level': 'error'
                    }) + '\n'
                else:
                    # Create message with attachment from memory
                    msg = create_message_with_attachment(
                        sender_email, sender_name, recipient, subject, body,
                        cv_data, cv_filename
                    )

                    # Send email
                    server.sendmail(sender_email, recipient, msg.as_string())
                    sent += 1

                    yield json.dumps({
                        'type': 'log',
                        'message': f'Email sent to: {recipient}',
                        'level': 'success'
                    }) + '\n'

            except Exception as e:
                failed += 1
                yield json.dumps({
                    'type': 'log',
                    'message': f'Failed to send to {recipient}: {str(e)}',
                    'level': 'error'
                }) + '\n'

            # Send progress update
            pending = total - sent - failed
            progress = int((i / total) * 100)

            yield json.dumps({
                'type': 'progress',
                'progress': progress,
                'total': total,
                'sent': sent,
                'failed': failed,
                'pending': pending
            }) + '\n'

            # Small delay to prevent rate limiting
            time.sleep(0.5)

        # Close connection
        server.quit()

        # Send completion message
        yield json.dumps({
            'type': 'log',
            'message': f'Campaign completed! Sent: {sent}, Failed: {failed}',
            'level': 'success'
        }) + '\n'

        yield json.dumps({
            'type': 'complete',
            'sent': sent,
            'failed': failed
        }) + '\n'

    except smtplib.SMTPAuthenticationError as e:
        error_msg = str(e)
        if 'Username and Password not accepted' in error_msg:
            yield json.dumps({
                'type': 'log',
                'message': 'Authentication failed! For Gmail, you need to use an App Password, not your regular password.',
                'level': 'error'
            }) + '\n'
            yield json.dumps({
                'type': 'log',
                'message': 'Please go to https://myaccount.google.com/apppasswords to generate one.',
                'level': 'error'
            }) + '\n'
        else:
            yield json.dumps({
                'type': 'log',
                'message': f'Authentication failed: {error_msg}',
                'level': 'error'
            }) + '\n'
    except smtplib.SMTPException as e:
        yield json.dumps({
            'type': 'log',
            'message': f'SMTP error: {e}',
            'level': 'error'
        }) + '\n'
    except Exception as e:
        yield json.dumps({
            'type': 'log',
            'message': f'Unexpected error: {e}',
            'level': 'error'
        }) + '\n'


@app.route('/send-emails', methods=['POST'])
def send_emails():
    """API endpoint to send emails"""
    try:
        # Get form data
        sender_email = request.form.get('sender_email')
        sender_name = request.form.get(
            'sender_name', '')  # Optional display name
        password = request.form.get('password')
        subject = request.form.get('subject')
        body = request.form.get('body')
        recipients = json.loads(request.form.get('recipients'))

        # Validate inputs
        if not all([sender_email, password, subject, body, recipients]):
            return jsonify({'error': 'Missing required fields'}), 400

        # Handle file upload - read into memory instead of saving to disk
        if 'cv' not in request.files:
            return jsonify({'error': 'No CV file uploaded'}), 400

        cv_file = request.files['cv']
        if cv_file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        if not (cv_file and allowed_file(cv_file.filename)):
            return jsonify({'error': 'Invalid file type. Only PDF allowed'}), 400

        # Read file data into memory
        cv_file.seek(0)  # Ensure we're at the beginning of the file
        cv_data = cv_file.read()
        cv_filename = secure_filename(cv_file.filename)

        # Verify we have file data
        if not cv_data:
            return jsonify({'error': 'Failed to read CV file'}), 500

        # Log file info for debugging
        print(f"CV file loaded: {cv_filename}, Size: {len(cv_data)} bytes")
        print(
            f"Sender display name: {sender_name if sender_name else '(not set)'}")

        # Return streaming response
        return Response(
            stream_with_context(send_emails_generator(
                sender_email, sender_name, password, subject, body, recipients,
                cv_data, cv_filename
            )),
            mimetype='application/x-ndjson'
        )

    except Exception as e:
        print(f"Error in send_emails: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/test-email', methods=['POST'])
def test_email():
    """Test endpoint to verify email configuration"""
    try:
        sender_email = request.json.get('sender_email')
        password = request.json.get('password')

        # Try to connect and authenticate
        server = smtplib.SMTP('smtp.gmail.com', 587)
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
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'version': '1.0'}), 200


if __name__ == '__main__':
    # Get port from environment variable
    port = int(os.environ.get('PORT', 5000))

    print("=" * 50)
    print("Job Application Email Sender - Backend")
    print("=" * 50)
    print(f"Server running on: http://localhost:{port}")
    print("Make sure to use Gmail App Password, not your regular password!")
    print("Generate one at: https://myaccount.google.com/apppasswords")
    print("=" * 50)

    app.run(debug=True, port=port, host='0.0.0.0')
