import java.io.File;

class CommandExamples {
    void runtimeScalar(String command, String[] environment, File directory) throws Exception {
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(command);
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(command, environment);
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(command, environment, directory);
        Runtime runtime = Runtime.getRuntime();
        // ruleid: java-runtime-exec-command-injection
        runtime.exec(command);
        // ruleid: java-runtime-exec-command-injection
        runtime.exec(command + " --version");
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec("echo safe", environment, directory);
    }

    void builders(String executable, String argument) {
        // ruleid: java-runtime-exec-command-injection
        new ProcessBuilder(executable);
        // ruleid: java-runtime-exec-command-injection
        new ProcessBuilder(executable, "--version");
        ProcessBuilder builder = new ProcessBuilder("echo");
        // ruleid: java-runtime-exec-command-injection
        builder.command(executable, argument);
        // ok: java-runtime-exec-command-injection
        new ProcessBuilder("echo", argument);
        // ok: java-runtime-exec-command-injection
        builder.command("echo", argument);
    }

    void arrays(String executable, String argument, String[] commands) throws Exception {
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(commands);
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(commands, null, new File("."));
        // ruleid: java-runtime-exec-command-injection
        new ProcessBuilder(commands);
        ProcessBuilder builder = new ProcessBuilder("echo");
        // ruleid: java-runtime-exec-command-injection
        builder.command(commands);
        // ruleid: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(new String[] {executable, "--version"});
        // ruleid: java-runtime-exec-command-injection
        new ProcessBuilder(new String[] {executable, argument});
        // ruleid: java-runtime-exec-command-injection
        builder.command(new String[] {executable, argument});
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(new String[] {"echo", argument});
        // ok: java-runtime-exec-command-injection
        new ProcessBuilder(new String[] {"echo", argument});
        String[] fixedProgram = {"echo", argument};
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(fixedProgram);
        // ok: java-runtime-exec-command-injection
        new ProcessBuilder(fixedProgram);
        java.util.List<String> fixedList = java.util.List.of("echo", argument);
        // ok: java-runtime-exec-command-injection
        new ProcessBuilder(fixedList);
    }

    void constants() throws Exception {
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec("echo safe");
        final String fixed = "echo";
        // ok: java-runtime-exec-command-injection
        new ProcessBuilder(fixed, "safe");
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec(fixed);
        // ok: java-runtime-exec-command-injection
        Runtime.getRuntime().exec("echo" + " safe");
    }

    void qualified(java.lang.String command, java.lang.String[] commands, java.lang.String argument) throws Exception {
        // ruleid: java-runtime-exec-command-injection
        java.lang.Runtime.getRuntime().exec(command);
        // ruleid: java-runtime-exec-command-injection
        new java.lang.ProcessBuilder(command, "--version");
        java.lang.Runtime runtime = java.lang.Runtime.getRuntime();
        // ruleid: java-runtime-exec-command-injection
        runtime.exec(command);
        // ruleid: java-runtime-exec-command-injection
        runtime.exec(commands);
        java.lang.ProcessBuilder builder = new java.lang.ProcessBuilder("echo");
        // ruleid: java-runtime-exec-command-injection
        builder.command(command, argument);
        // ruleid: java-runtime-exec-command-injection
        builder.command(commands);
        // ruleid: java-runtime-exec-command-injection
        java.lang.Runtime.getRuntime().exec(new java.lang.String[] {command, "--version"});
        // ruleid: java-runtime-exec-command-injection
        new java.lang.ProcessBuilder(new java.lang.String[] {command, argument});
        // ruleid: java-runtime-exec-command-injection
        builder.command(new java.lang.String[] {command, argument});
        // ok: java-runtime-exec-command-injection
        builder.command(new java.lang.String[] {"echo", argument});
    }

    void unrelatedMethod(Unrelated tool, String command) {
        // ok: java-runtime-exec-command-injection
        tool.command(command);
    }
}

class Unrelated {
    void command(String value) {}
}
