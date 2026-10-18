import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.zip.DataFormatException;
import java.util.zip.Deflater;
import java.util.zip.Inflater;

public final class Bot implements Runnable {
    static final int PROTOCOL = 777;

    static final int INTENTION = 0;
    static final int LOGIN_HELLO = 0;
    static final int LOGIN_CUSTOM_QUERY_ANSWER = 2;
    static final int LOGIN_ACKNOWLEDGED = 3;
    static final int CONFIG_CLIENT_INFORMATION = 0;
    static final int CONFIG_FINISH = 3;
    static final int CONFIG_KEEP_ALIVE = 4;
    static final int CONFIG_PONG = 5;
    static final int CONFIG_KNOWN_PACKS = 7;
    static final int CONFIG_ACCEPT_CODE_OF_CONDUCT = 9;
    static final int PLAY_ACCEPT_TELEPORTATION = 0;
    static final int PLAY_CHUNK_BATCH_RECEIVED = 11;
    static final int PLAY_CLIENT_TICK_END = 13;
    static final int PLAY_CONFIGURATION_ACKNOWLEDGED = 16;
    static final int PLAY_KEEP_ALIVE = 28;
    static final int PLAY_MOVE_PLAYER_POS = 30;
    static final int PLAY_PLAYER_LOADED = 44;
    static final int PLAY_PONG = 45;

    static final int LOGIN_DISCONNECT = 0;
    static final int LOGIN_ENCRYPTION = 1;
    static final int LOGIN_FINISHED = 2;
    static final int LOGIN_COMPRESSION = 3;
    static final int LOGIN_CUSTOM_QUERY = 4;
    static final int CONFIG_DISCONNECT_IN = 2;
    static final int CONFIG_FINISH_IN = 3;
    static final int CONFIG_KEEP_ALIVE_IN = 4;
    static final int CONFIG_PING = 5;
    static final int CONFIG_SELECT_KNOWN_PACKS = 15;
    static final int CONFIG_CODE_OF_CONDUCT = 20;
    static final int PLAY_CHUNK_BATCH_FINISHED = 11;
    static final int PLAY_DISCONNECT = 32;
    static final int PLAY_KEEP_ALIVE_IN = 45;
    static final int PLAY_PING = 62;
    static final int PLAY_PLAYER_POSITION = 73;
    static final int PLAY_START_CONFIGURATION = 120;

    enum Phase {
        LOGIN, CONFIGURATION, PLAY
    }

    final String host;
    final int port;
    final String name;
    final boolean fly;
    Socket socket;
    DataInputStream in;
    OutputStream out;
    int threshold = -1;
    Phase phase = Phase.LOGIN;
    volatile boolean playing;
    volatile double x, y, z;
    volatile float yaw, pitch;
    volatile double anchorX, anchorY, anchorZ;
    volatile long anchoredAt;

    Bot(String host, int port, String name, boolean fly) {
        this.host = host;
        this.port = port;
        this.name = name;
        this.fly = fly;
    }

    public static void main(String[] args) throws Exception {
        String host = args[0];
        int port = Integer.parseInt(args[1]);
        int count = Integer.parseInt(args[2]);
        boolean fly = args.length < 4 || args[3].equals("fly");
        Thread[] threads = new Thread[count];
        for (int i = 0; i < count; i++) {
            threads[i] = new Thread(new Bot(host, port, String.format("bot%02d", i), fly), "bot" + i);
            threads[i].start();
            Thread.sleep(200);
        }
        for (Thread thread : threads) {
            thread.join();
        }
    }

    @Override
    public void run() {
        try {
            connect();
        } catch (Exception e) {
            System.out.println("BOT FAIL " + name + ": " + e);
        } finally {
            try {
                if (socket != null) {
                    socket.close();
                }
            } catch (IOException ignored) {
            }
        }
    }

    void connect() throws Exception {
        socket = new Socket(host, port);
        socket.setTcpNoDelay(true);
        in = new DataInputStream(socket.getInputStream());
        out = socket.getOutputStream();

        Buffer handshake = new Buffer(INTENTION).varInt(PROTOCOL).string(host).u16(port).varInt(2);
        send(handshake);
        UUID uuid = UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(StandardCharsets.UTF_8));
        send(new Buffer(LOGIN_HELLO).string(name).uuid(uuid));

        Thread mover = new Thread(this::move, name + "-move");
        mover.setDaemon(true);
        mover.start();
        while (true) {
            DataInputStream packet = receive();
            int id = readVarInt(packet);
            switch (phase) {
                case LOGIN -> login(id, packet);
                case CONFIGURATION -> configuration(id, packet);
                case PLAY -> play(id, packet);
            }
        }
    }

    void login(int id, DataInputStream packet) throws IOException {
        switch (id) {
            case LOGIN_DISCONNECT -> throw new IOException("disconnected during login");
            case LOGIN_ENCRYPTION -> throw new IOException("server requires online mode");
            case LOGIN_COMPRESSION -> threshold = readVarInt(packet);
            case LOGIN_CUSTOM_QUERY ->
                send(new Buffer(LOGIN_CUSTOM_QUERY_ANSWER).varInt(readVarInt(packet)).bool(false));
            case LOGIN_FINISHED -> {
                send(new Buffer(LOGIN_ACKNOWLEDGED));
                phase = Phase.CONFIGURATION;
                clientInformation();
            }
            default -> {
            }
        }
    }

    void clientInformation() throws IOException {
        Buffer info = new Buffer(CONFIG_CLIENT_INFORMATION)
                .string("en_us")
                .u8(10)
                .varInt(0)
                .bool(true)
                .u8(0x7f)
                .varInt(1)
                .bool(false)
                .bool(true)
                .varInt(0);
        send(info);
    }

    void configuration(int id, DataInputStream packet) throws IOException {
        switch (id) {
            case CONFIG_DISCONNECT_IN -> throw new IOException("disconnected during configuration");
            case CONFIG_KEEP_ALIVE_IN -> send(new Buffer(CONFIG_KEEP_ALIVE).i64(packet.readLong()));
            case CONFIG_PING -> send(new Buffer(CONFIG_PONG).i32(packet.readInt()));
            case CONFIG_SELECT_KNOWN_PACKS -> send(new Buffer(CONFIG_KNOWN_PACKS).raw(packet.readAllBytes()));
            case CONFIG_CODE_OF_CONDUCT -> send(new Buffer(CONFIG_ACCEPT_CODE_OF_CONDUCT));
            case CONFIG_FINISH_IN -> {
                send(new Buffer(CONFIG_FINISH));
                phase = Phase.PLAY;
            }
            default -> {
            }
        }
    }

    void play(int id, DataInputStream packet) throws IOException {
        switch (id) {
            case PLAY_DISCONNECT -> throw new IOException("disconnected during play");
            case PLAY_KEEP_ALIVE_IN -> send(new Buffer(PLAY_KEEP_ALIVE).i64(packet.readLong()));
            case PLAY_PING -> send(new Buffer(PLAY_PONG).i32(packet.readInt()));
            case PLAY_CHUNK_BATCH_FINISHED -> send(new Buffer(PLAY_CHUNK_BATCH_RECEIVED).f32(25f));
            case PLAY_START_CONFIGURATION -> {
                playing = false;
                send(new Buffer(PLAY_CONFIGURATION_ACKNOWLEDGED));
                phase = Phase.CONFIGURATION;
            }
            case PLAY_PLAYER_POSITION -> teleported(packet);
            default -> {
            }
        }
    }

    void teleported(DataInputStream packet) throws IOException {
        int teleport = readVarInt(packet);
        double px = packet.readDouble();
        double py = packet.readDouble();
        double pz = packet.readDouble();
        packet.skipNBytes(3 * 8);
        float pyaw = packet.readFloat();
        float ppitch = packet.readFloat();
        int relative = packet.readInt();
        double ax;
        double ay;
        double az;
        synchronized (this) {
            x = (relative & 1) != 0 ? x + px : px;
            y = (relative & 2) != 0 ? y + py : py;
            z = (relative & 4) != 0 ? z + pz : pz;
            yaw = (relative & 8) != 0 ? yaw + pyaw : pyaw;
            pitch = (relative & 16) != 0 ? pitch + ppitch : ppitch;
            anchorX = ax = x;
            anchorY = ay = y;
            anchorZ = az = z;
            anchoredAt = System.nanoTime();
        }
        send(new Buffer(PLAY_ACCEPT_TELEPORTATION).varInt(teleport).f64(ax).f64(ay).f64(az).f32(yaw).f32(pitch));
        if (!playing) {
            send(new Buffer(PLAY_PLAYER_LOADED));
            playing = true;
            System.out.println("BOT READY " + name);
        }
    }

    void move() {
        long next = System.nanoTime();
        try {
            while (!socket.isClosed()) {
                next += 50_000_000L;
                if (playing) {
                    if (fly) {
                        double angle;
                        double cx;
                        double cy;
                        double cz;
                        synchronized (this) {
                            angle = (System.nanoTime() - anchoredAt) / 1e9 * (2 * Math.PI / 20);
                            cx = anchorX;
                            cy = anchorY;
                            cz = anchorZ;
                        }
                        double nx = cx + 16 * Math.sin(angle);
                        double nz = cz + 16 * (1 - Math.cos(angle));
                        send(new Buffer(PLAY_MOVE_PLAYER_POS).f64(nx).f64(cy).f64(nz).u8(0));
                        x = nx;
                        z = nz;
                    }
                    send(new Buffer(PLAY_CLIENT_TICK_END));
                }
                long sleep = next - System.nanoTime();
                if (sleep > 0) {
                    Thread.sleep(sleep / 1_000_000, (int) (sleep % 1_000_000));
                }
            }
        } catch (IOException | InterruptedException e) {
        }
    }

    synchronized void send(Buffer packet) throws IOException {
        byte[] body = packet.bytes();
        ByteArrayOutputStream frame = new ByteArrayOutputStream();
        if (threshold >= 0) {
            ByteArrayOutputStream inner = new ByteArrayOutputStream();
            if (body.length >= threshold) {
                writeVarInt(inner, body.length);
                Deflater deflater = new Deflater();
                deflater.setInput(body);
                deflater.finish();
                byte[] chunk = new byte[8192];
                while (!deflater.finished()) {
                    inner.write(chunk, 0, deflater.deflate(chunk));
                }
                deflater.end();
            } else {
                writeVarInt(inner, 0);
                inner.write(body);
            }
            body = inner.toByteArray();
        }
        writeVarInt(frame, body.length);
        frame.write(body);
        out.write(frame.toByteArray());
        out.flush();
    }

    DataInputStream receive() throws IOException, DataFormatException {
        int length = readVarInt(in);
        byte[] data = in.readNBytes(length);
        if (data.length != length) {
            throw new EOFException("connection closed");
        }
        if (threshold >= 0) {
            DataInputStream framed = new DataInputStream(new ByteArrayInputStream(data));
            int size = readVarInt(framed);
            byte[] rest = framed.readAllBytes();
            if (size == 0) {
                data = rest;
            } else {
                Inflater inflater = new Inflater();
                inflater.setInput(rest);
                data = new byte[size];
                int done = 0;
                while (done < size) {
                    int n = inflater.inflate(data, done, size - done);
                    if (n == 0 && (inflater.finished() || inflater.needsInput())) {
                        throw new IOException("truncated compressed packet");
                    }
                    done += n;
                }
                inflater.end();
            }
        }
        return new DataInputStream(new ByteArrayInputStream(data));
    }

    static int readVarInt(InputStream in) throws IOException {
        int value = 0;
        for (int shift = 0; shift < 35; shift += 7) {
            int b = in.read();
            if (b < 0) {
                throw new EOFException("connection closed");
            }
            value |= (b & 0x7f) << shift;
            if ((b & 0x80) == 0) {
                return value;
            }
        }
        throw new IOException("varint too long");
    }

    static void writeVarInt(OutputStream out, int value) throws IOException {
        while ((value & ~0x7f) != 0) {
            out.write((value & 0x7f) | 0x80);
            value >>>= 7;
        }
        out.write(value);
    }

    static final class Buffer {
        final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        final DataOutputStream data = new DataOutputStream(bytes);

        Buffer(int id) throws IOException {
            varInt(id);
        }

        Buffer varInt(int value) throws IOException {
            writeVarInt(bytes, value);
            return this;
        }

        Buffer string(String value) throws IOException {
            byte[] utf8 = value.getBytes(StandardCharsets.UTF_8);
            varInt(utf8.length);
            bytes.write(utf8);
            return this;
        }

        Buffer u8(int value) {
            bytes.write(value);
            return this;
        }

        Buffer u16(int value) throws IOException {
            data.writeShort(value);
            return this;
        }

        Buffer i32(int value) throws IOException {
            data.writeInt(value);
            return this;
        }

        Buffer i64(long value) throws IOException {
            data.writeLong(value);
            return this;
        }

        Buffer f32(float value) throws IOException {
            data.writeFloat(value);
            return this;
        }

        Buffer f64(double value) throws IOException {
            data.writeDouble(value);
            return this;
        }

        Buffer bool(boolean value) {
            bytes.write(value ? 1 : 0);
            return this;
        }

        Buffer uuid(UUID value) throws IOException {
            data.writeLong(value.getMostSignificantBits());
            data.writeLong(value.getLeastSignificantBits());
            return this;
        }

        Buffer raw(byte[] value) throws IOException {
            bytes.write(value);
            return this;
        }

        byte[] bytes() {
            return bytes.toByteArray();
        }
    }
}
