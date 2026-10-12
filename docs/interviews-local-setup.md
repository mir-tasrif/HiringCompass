# Interview setup on one Wi-Fi network

## Candidate joins without installing a certificate

For a local demonstration, use a temporary, publicly trusted HTTPS tunnel. The browser page and LiveKit signaling share that one link; the video media still uses the recruiter computer's Wi-Fi address. The candidate needs an internet connection as well as access to the same Wi-Fi network.

1. Confirm `.env` has the SMTP credentials, `GROQ_API_KEY`, and LiveKit API key and secret. From PowerShell in the project directory, run:

   ```powershell
   .\scripts\start-interview-demo.ps1 -LanIp 192.168.150.45
   ```

   Replace the IP if the recruiter computer's Wi-Fi address changes. The command starts the required Docker services, obtains a temporary `https://...trycloudflare.com` address, and updates `WEB_BASE_URL` and `LIVEKIT_PUBLIC_URL` in `.env`. It does not print credentials.

2. Open the **recruiter address printed by the command**. Schedule a new interview or resend an existing invitation. The candidate opens the newest emailed link directly in a current browser. Neither device installs a certificate.

3. Keep the tunnel running until the interview ends. If the tunnel container restarts, its address changes: run the command again and resend outstanding invitations. For video media, allow TCP 7881 and UDP 7882 through the recruiter computer's firewall and disable Wi-Fi client isolation. TCP 8443 and 8444 are not used in this tunnel mode.

This temporary address exposes the app's web login and token-protected candidate invitation to the internet while the tunnel runs. It is intended only for testing and has no uptime guarantee. Stop it with `docker compose --profile interview-tunnel stop interview-tunnel` when finished. For a durable deployment, use a stable domain and hosted signaling.

## Alternative: LAN-only HTTPS with a trusted local certificate

The recruiter and candidate must be on the same Wi-Fi network. The recruiter computer runs Docker and exposes an HTTPS web address plus a secure LiveKit WebSocket address. Browsers require a trusted HTTPS origin to grant camera and microphone access on a LAN IP address.

1. Find the recruiter computer's Wi-Fi IPv4 address with `ipconfig`. Use that address in `.env`:

   ```dotenv
   LAN_HOST=192.168.150.45
   WEB_BASE_URL=https://192.168.150.45:8443
   LIVEKIT_PUBLIC_URL=wss://192.168.150.45:8444
   STT_PROVIDER=groq
   GROQ_API_KEY=your-groq-key
   INTERVIEW_EMAIL=your-address@gmail.com
   INTERVIEW_EMAIL_PASSWORD=your-smtp-app-password
   INTERVIEW_SMTP_HOST=smtp.gmail.com
   INTERVIEW_SMTP_PORT=587
   ```

   Replace the example IP and credentials. If your mailbox is not Gmail, set `INTERVIEW_SMTP_HOST` and `INTERVIEW_SMTP_PORT` to your provider's SMTP server. Do not send a normal account password if your provider requires an app password.

2. Start the API, worker, web app, LiveKit, and local HTTPS gateway:

   ```powershell
   docker compose --profile lan up --build -d db api worker web livekit lan-gateway
   ```

3. Export Caddy's local root certificate. This certificate must be trusted on each device that will use the interview UI:

   ```powershell
   docker compose cp lan-gateway:/data/caddy/pki/authorities/local/root.crt .\hiringcompass-local-root.crt
   certutil -user -addstore Root .\hiringcompass-local-root.crt
   ```

   Transfer the same certificate to the candidate device and import it into that device's trusted certificate store. The candidate opens the private URL received by email. Keep this local CA certificate within the test group and remove its trust after the local demonstration if it is no longer needed.

4. Open `WEB_BASE_URL` on the recruiter computer. Both devices must be able to reach the LAN IP on TCP ports **8443**, **8444**, and **7881**, plus UDP port **7882**. Allow these ports through the host firewall if the devices cannot connect.

5. In **Candidates → Selected for Interview**, choose **Schedule interview**. Confirm or correct the candidate email. The invitation link rotates on reschedule or resend. The candidate acknowledges transcription and waits; the recruiter admits them and starts the session.

Each browser sends short audio segments from its own microphone to Groq Whisper. The backend stores only speaker-labeled transcript turns; it does not keep raw audio or video files. If transcription fails, the call continues and the browser retries queued segments while the page remains open. The recruiter alone can view the live and saved transcript.

The candidate link is valid only for that interview and expires after its scheduled window. The LiveKit room token is issued only after the recruiter starts and the candidate is admitted.
