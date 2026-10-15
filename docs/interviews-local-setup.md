# Interview setup for candidates on any network

## Candidate joins without installing a certificate

For candidates on different networks, the Cloudflare tunnel serves the browser page while LiveKit Cloud carries video and audio. The candidate needs only a current browser, internet access, and camera/microphone permission. The recruiter computer and Docker stack must remain running during the call.

1. Confirm `.env` has the SMTP credentials, `LIVEKIT_URL=wss://<your-project>.livekit.cloud`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`. For live AssemblyAI transcription, set `STT_PROVIDER=assemblyai` and `ASSEMBLYAI_API_KEY`; to keep Groq Whisper, set `STT_PROVIDER=groq` and `GROQ_API_KEY`. From PowerShell in the project directory, run:

   ```powershell
   .\scripts\start-interview-demo.ps1 
   ```

   This command verifies the LiveKit Cloud credentials, starts the required Docker services, creates a temporary `https://...trycloudflare.com` address, updates `WEB_BASE_URL` and `LIVEKIT_PUBLIC_URL` in `.env`, and starts the API after the tunnel is reachable. The API emails replacement links to sent invitations for unstarted interviews when their recorded hostname differs. It does not print credentials.

2. Open the **recruiter address printed by the command**. Schedule a new interview or resend an existing invitation. The candidate opens the newest emailed link directly in a current browser. Neither device installs a certificate.

3. Keep the tunnel running until the interview ends. If you stop or restart the system, run the same command again; the script creates a fresh hostname and the app automatically emails updated links for pending, unstarted interviews. No inbound video ports or shared Wi-Fi are needed with LiveKit Cloud.

This temporary address exposes the app's web login and token-protected candidate invitation to the internet while the tunnel runs. It is intended only for testing and has no uptime guarantee. Stop it with `docker compose --profile interview-tunnel stop interview-tunnel` when finished. For a durable invitation address, use a stable app domain.

## Alternative: LAN-only HTTPS with a trusted local certificate

For the older local LiveKit mode, the recruiter and candidate must be on the same Wi-Fi network. Set `LIVEKIT_URL=ws://livekit:7880` and use `-LanIp` with the startup script, or follow the LAN certificate setup below. Browsers require a trusted HTTPS origin to grant camera and microphone access on a LAN IP address.

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

With AssemblyAI selected, each browser sends its own microphone audio through the app's authenticated WebSocket to AssemblyAI's streaming API. Only finalized, speaker-labeled transcript turns are saved; the app does not keep raw audio or video files. If transcription disconnects, the call continues and the browser reconnects, although speech during an outage may be missed. The recruiter alone can view the live and saved transcript. AssemblyAI offers zero audio and transcript retention for Streaming when your AssemblyAI account is opted out of model training; review that setting in the AssemblyAI dashboard if this privacy requirement applies.

The candidate link is valid only for that interview and expires after its scheduled window. The LiveKit room token is issued only after the recruiter starts and the candidate is admitted.
