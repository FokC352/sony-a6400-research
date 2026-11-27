// @category A6400.Research
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.importer.AutoImporter;
import ghidra.app.util.importer.MessageLog;
import ghidra.app.util.opinion.LoadResults;
import ghidra.framework.model.DomainFile;
import ghidra.framework.model.DomainFolder;
import ghidra.framework.model.Project;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Program;
import ghidra.program.model.symbol.Symbol;
import ghidra.util.task.TaskMonitor;
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.math.BigInteger;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public class PersistentSymbolDaemon extends GhidraScript {

    private static final class CachedProgram {
        final String sha256;
        final String filename;
        final Program program;
        final DecompInterface decompiler;

        CachedProgram(String sha256, String filename, Program program, DecompInterface decompiler) {
            this.sha256 = sha256;
            this.filename = filename;
            this.program = program;
            this.decompiler = decompiler;
        }
    }

    private final Map<String, CachedProgram> cache = new ConcurrentHashMap<>();
    private final Gson gson = new Gson();
    private volatile boolean stopRequested = false;

    private static String sha256Hex(File file) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] buf = new byte[1 << 20];
        try (var in = Files.newInputStream(file.toPath())) {
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
            }
        }
        StringBuilder sb = new StringBuilder(64);
        for (byte b : md.digest()) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }

    private synchronized CachedProgram loadOrGetProgram(File elfFile, String expectedSha) throws Exception {
        if (!elfFile.isFile()) {
            throw new IllegalArgumentException("ELF file does not exist: " + elfFile);
        }
        String actualSha = sha256Hex(elfFile);
        if (expectedSha != null && !expectedSha.isEmpty() && !expectedSha.equalsIgnoreCase(actualSha)) {
            throw new IllegalArgumentException(
                "SHA-256 mismatch for " + elfFile + ": expected " + expectedSha + ", got " + actualSha);
        }
        CachedProgram existing = cache.get(actualSha);
        if (existing != null && !existing.program.isClosed()) {
            return existing;
        }

        Project project = state.getProject();
        if (project == null) {
            throw new IllegalStateException("Headless Ghidra state has no active project");
        }
        DomainFolder rootFolder = project.getProjectData().getRootFolder();
        String progName = elfFile.getName() + "_" + actualSha.substring(0, 12);
        DomainFile existingFile = rootFolder.getFile(progName);
        Program prog;
        if (existingFile != null) {
            prog = (Program) existingFile.getDomainObject(this, true, false, TaskMonitor.DUMMY);
        } else {
            MessageLog msgLog = new MessageLog();
            LoadResults<Program> loaded = AutoImporter.importByUsingBestGuess(
                elfFile, project, "/", this, msgLog, TaskMonitor.DUMMY);
            prog = loaded.getPrimaryDomainObject(this);
            // Persist imported program under its hash-tagged name for fast restart recovery.
            int tx = prog.startTransaction("set-name");
            try {
                prog.setName(progName);
            } finally {
                prog.endTransaction(tx, true);
            }
            loaded.save(TaskMonitor.DUMMY);
        }

        DecompInterface ifc = new DecompInterface();
        if (!ifc.openProgram(prog)) {
            throw new IllegalStateException("DecompInterface failed to open " + progName);
        }
        CachedProgram entry = new CachedProgram(actualSha, elfFile.getName(), prog, ifc);
        cache.put(actualSha, entry);
        return entry;
    }

    private JsonObject handleDecompile(JsonObject req) throws Exception {
        String elfPath = req.get("elf_path").getAsString();
        String expectedSha = req.has("sha256") ? req.get("sha256").getAsString() : "";
        long vaRaw = req.get("va").getAsLong();
        long elfBase = req.has("elf_base") ? req.get("elf_base").getAsLong() : 0L;
        boolean thumbHint = req.has("thumb") ? req.get("thumb").getAsBoolean() : ((vaRaw & 1L) != 0);
        int timeoutSec = req.has("timeout_sec") ? req.get("timeout_sec").getAsInt() : 60;
        if (timeoutSec < 1 || timeoutSec > 300) {
            timeoutSec = 60;
        }

        long t0 = System.nanoTime();
        boolean wasCached = false;
        if (!expectedSha.isEmpty() && cache.containsKey(expectedSha.toLowerCase())) {
            wasCached = true;
        }
        CachedProgram cp = loadOrGetProgram(new File(elfPath), expectedSha);
        Program prog = cp.program;

        long relOffset = (vaRaw & ~1L) - elfBase;
        if (relOffset < 0) {
            relOffset = vaRaw & ~1L;
        }
        Address entry = prog.getImageBase().add(relOffset);
        if (!prog.getMemory().contains(entry)) {
            throw new IllegalArgumentException(
                String.format("Address %s (va=0x%x base=0x%x) not in loaded memory", entry, vaRaw, elfBase));
        }

        FunctionManager fm = prog.getFunctionManager();
        Function func = fm.getFunctionAt(entry);
        if (func == null) {
            func = fm.getFunctionContaining(entry);
        }
        if (func == null) {
            int tx = prog.startTransaction("disassemble-and-create-func");
            try {
                Register thumbReg = prog.getRegister("TMode");
                if (thumbReg != null && thumbHint) {
                    Address endHint = entry.add(0x4000);
                    if (!prog.getMemory().contains(endHint)) {
                        endHint = prog.getMemory().getBlock(entry).getEnd();
                    }
                    prog.getProgramContext().setValue(thumbReg, entry, endHint, BigInteger.ONE);
                }
                AddressSet bound = new AddressSet(entry,
                    prog.getMemory().getBlock(entry).getEnd().compareTo(entry.add(0x4000)) < 0
                        ? prog.getMemory().getBlock(entry).getEnd()
                        : entry.add(0x4000));
                DisassembleCommand dis = new DisassembleCommand(entry, bound, true);
                dis.applyTo(prog, TaskMonitor.DUMMY);
                CreateFunctionCmd cf = new CreateFunctionCmd(entry);
                cf.applyTo(prog, TaskMonitor.DUMMY);
                func = fm.getFunctionAt(entry);
                if (func == null) {
                    Symbol sym = prog.getSymbolTable().getPrimarySymbol(entry);
                    String fname = sym != null ? sym.getName() : String.format("FUN_%08x", entry.getOffset());
                    func = fm.createFunction(fname, entry, new AddressSet(entry),
                        ghidra.program.model.symbol.SourceType.USER_DEFINED);
                }
            } finally {
                prog.endTransaction(tx, true);
            }
        }

        if (func == null) {
            throw new IllegalStateException("Failed to resolve or create function at " + entry);
        }

        DecompileResults res;
        synchronized (cp) {
            res = cp.decompiler.decompileFunction(func, timeoutSec, TaskMonitor.DUMMY);
        }
        if (res == null || !res.decompileCompleted() || res.getDecompiledFunction() == null) {
            String err = res != null ? res.getErrorMessage() : "null decompile result";
            throw new IllegalStateException("Decompilation failed at " + entry + ": " + err);
        }

        long elapsedMs = (System.nanoTime() - t0) / 1_000_000L;
        JsonObject out = new JsonObject();
        out.addProperty("ok", true);
        out.addProperty("sha256", cp.sha256);
        out.addProperty("va", vaRaw);
        out.addProperty("ghidra_addr", entry.toString());
        out.addProperty("func_name", func.getName());
        out.addProperty("c_code", res.getDecompiledFunction().getC());
        out.addProperty("cached_program", wasCached);
        out.addProperty("elapsed_ms", elapsedMs);
        out.addProperty("ghidra_version", ghidra.framework.Application.getApplicationVersion());
        return out;
    }

    @Override
    public void run() throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("A6400_GHIDRA_PORT", "47640"));
        String token = System.getenv().getOrDefault("A6400_GHIDRA_TOKEN", "");
        String stateFile = System.getenv().getOrDefault("A6400_GHIDRA_STATE", "");
        long pid = ProcessHandle.current().pid();
        String ghidraVer = ghidra.framework.Application.getApplicationVersion();

        try (ServerSocket server = new ServerSocket(port, 32, InetAddress.getByName("127.0.0.1"))) {
            if (!stateFile.isEmpty()) {
                JsonObject meta = new JsonObject();
                meta.addProperty("pid", pid);
                meta.addProperty("port", port);
                meta.addProperty("token", token);
                meta.addProperty("ghidra_version", ghidraVer);
                meta.addProperty("started_utc", Instant.now().toString());
                Path sp = Path.of(stateFile);
                Files.createDirectories(sp.getParent());
                Files.writeString(sp, gson.toJson(meta) + "\n", StandardCharsets.UTF_8);
            }
            println("PERSISTENT_SYMBOL_DAEMON READY port=" + port + " pid=" + pid);

            while (!stopRequested && !monitor.isCancelled()) {
                try (Socket client = server.accept();
                     BufferedReader reader = new BufferedReader(
                         new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
                     PrintWriter writer = new PrintWriter(
                         new OutputStreamWriter(client.getOutputStream(), StandardCharsets.UTF_8), true)) {
                    String line = reader.readLine();
                    if (line == null || line.isBlank()) {
                        continue;
                    }
                    JsonObject resp = new JsonObject();
                    try {
                        JsonObject req = JsonParser.parseString(line).getAsJsonObject();
                        String reqToken = req.has("token") ? req.get("token").getAsString() : "";
                        if (!token.isEmpty() && !token.equals(reqToken)) {
                            resp.addProperty("ok", false);
                            resp.addProperty("error", "unauthorized");
                        } else {
                            String op = req.has("op") ? req.get("op").getAsString() : "";
                            switch (op) {
                                case "ping" -> {
                                    resp.addProperty("ok", true);
                                    resp.addProperty("pid", pid);
                                    resp.addProperty("ghidra_version", ghidraVer);
                                    resp.addProperty("loaded_programs", cache.size());
                                }
                                case "shutdown" -> {
                                    resp.addProperty("ok", true);
                                    stopRequested = true;
                                }
                                case "decompile" -> resp = handleDecompile(req);
                                default -> {
                                    resp.addProperty("ok", false);
                                    resp.addProperty("error", "unknown op: " + op);
                                }
                            }
                        }
                    } catch (Throwable exc) {
                        resp = new JsonObject();
                        resp.addProperty("ok", false);
                        resp.addProperty("error", exc.getClass().getSimpleName() + ": " + exc.getMessage());
                    }
                    writer.println(gson.toJson(resp));
                }
            }
        } finally {
            for (CachedProgram cp : cache.values()) {
                try {
                    cp.decompiler.dispose();
                    cp.program.release(this);
                } catch (Throwable ignored) {
                }
            }
            cache.clear();
        }
    }
}
