/**
 * Performance Benchmark for Lock-Free Ring Buffer
 * 
 * Compares performance of lock-free ring buffers vs traditional queues
 * under various load patterns and concurrency scenarios.
 */

import { LockFreeRingBuffer, MPSCRingBuffer, LockFreeIngestionChannel } from "../src/queue/lockFreeRingBuffer";
import { BackpressureManager, IngestionPacket, PacketPriority } from "../src/queue/backpressure";
import { AsyncBoundedQueue } from "../src/queue/backpressure";

interface BenchmarkResult {
  name: string;
  operations: number;
  durationMs: number;
  opsPerSecond: number;
  averageLatency: number;
  peakLatency: number;
  failures: number;
  memoryUsage: number;
}

interface BenchmarkConfig {
  iterations: number;
  capacity: number;
  producerThreads: number;
  consumerThreads: number;
  dataSize: number;
}

class BenchmarkSuite {
  private results: BenchmarkResult[] = [];

  /**
   * Benchmark SPSC Lock-Free Ring Buffer
   */
  async benchmarkSPSCLockFree(config: BenchmarkConfig): Promise<BenchmarkResult> {
    const buffer = new LockFreeRingBuffer<IngestionPacket>({
      capacity: config.capacity,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });

    const startTime = Date.now();
    const startMemory = process.memoryUsage().heapUsed;

    let successes = 0;
    let failures = 0;
    const latencies: number[] = [];

    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i, value: Math.random() },
        timestamp: Date.now(),
      };

      const enqueueStart = Date.now();
      const enqueued = buffer.tryEnqueue(packet);
      const enqueueLatency = Date.now() - enqueueStart;

      if (enqueued) {
        successes++;
        latencies.push(enqueueLatency);
        buffer.tryDequeue(); // Consume immediately
      } else {
        failures++;
      }
    }

    const duration = Date.now() - startTime;
    const endMemory = process.memoryUsage().heapUsed;

    const result: BenchmarkResult = {
      name: "SPSC Lock-Free Ring Buffer",
      operations: successes,
      durationMs: duration,
      opsPerSecond: (successes / duration) * 1000,
      averageLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
      peakLatency: Math.max(...latencies),
      failures,
      memoryUsage: endMemory - startMemory,
    };

    this.results.push(result);
    return result;
  }

  /**
   * Benchmark MPSC Lock-Free Ring Buffer
   */
  async benchmarkMPSCLockFree(config: BenchmarkConfig): Promise<BenchmarkResult> {
    const buffer = new MPSCRingBuffer<IngestionPacket>({
      capacity: config.capacity,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });

    const startTime = Date.now();
    const startMemory = process.memoryUsage().heapUsed;

    let successes = 0;
    let failures = 0;
    const latencies: number[] = [];

    // Simulate multiple producers
    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i, value: Math.random() },
        timestamp: Date.now(),
      };

      const enqueueStart = Date.now();
      const enqueued = buffer.tryEnqueue(packet);
      const enqueueLatency = Date.now() - enqueueStart;

      if (enqueued) {
        successes++;
        latencies.push(enqueueLatency);
      } else {
        failures++;
      }

      // Consumer runs periodically
      if (i % 10 === 0) {
        buffer.tryDequeue();
      }
    }

    // Drain remaining items
    while (!buffer.isEmpty()) {
      buffer.tryDequeue();
    }

    const duration = Date.now() - startTime;
    const endMemory = process.memoryUsage().heapUsed;

    const result: BenchmarkResult = {
      name: "MPSC Lock-Free Ring Buffer",
      operations: successes,
      durationMs: duration,
      opsPerSecond: (successes / duration) * 1000,
      averageLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
      peakLatency: Math.max(...latencies),
      failures,
      memoryUsage: endMemory - startMemory,
    };

    this.results.push(result);
    return result;
  }

  /**
   * Benchmark Traditional Async Queue
   */
  async benchmarkTraditionalQueue(config: BenchmarkConfig): Promise<BenchmarkResult> {
    const queue = new AsyncBoundedQueue<IngestionPacket>(config.capacity);

    const startTime = Date.now();
    const startMemory = process.memoryUsage().heapUsed;

    let successes = 0;
    let failures = 0;
    const latencies: number[] = [];

    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i, value: Math.random() },
        timestamp: Date.now(),
      };

      const enqueueStart = Date.now();
      const enqueued = queue.tryPut(packet);
      const enqueueLatency = Date.now() - enqueueStart;

      if (enqueued) {
        successes++;
        latencies.push(enqueueLatency);
        queue.tryGet(); // Consume immediately
      } else {
        failures++;
      }
    }

    const duration = Date.now() - startTime;
    const endMemory = process.memoryUsage().heapUsed;

    const result: BenchmarkResult = {
      name: "Traditional Async Queue",
      operations: successes,
      durationMs: duration,
      opsPerSecond: (successes / duration) * 1000,
      averageLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
      peakLatency: Math.max(...latencies),
      failures,
      memoryUsage: endMemory - startMemory,
    };

    this.results.push(result);
    return result;
  }

  /**
   * Benchmark Backpressure Manager
   */
  async benchmarkBackpressureManager(config: BenchmarkConfig): Promise<BenchmarkResult> {
    const manager = new BackpressureManager({
      maxCapacity: config.capacity,
      dropThreshold: 0.9,
      slowDownThreshold: 0.7,
      slowDownDelay: 0, // Disable slow down for benchmark
      enableMetrics: true,
    });

    const startTime = Date.now();
    const startMemory = process.memoryUsage().heapUsed;

    let successes = 0;
    let failures = 0;
    const latencies: number[] = [];

    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i, value: Math.random() },
        timestamp: Date.now(),
      };

      const enqueueStart = Date.now();
      const enqueued = await manager.enqueue(packet);
      const enqueueLatency = Date.now() - enqueueStart;

      if (enqueued) {
        successes++;
        latencies.push(enqueueLatency);
        manager.tryDequeue(); // Consume immediately
      } else {
        failures++;
      }
    }

    const duration = Date.now() - startTime;
    const endMemory = process.memoryUsage().heapUsed;

    const result: BenchmarkResult = {
      name: "Backpressure Manager",
      operations: successes,
      durationMs: duration,
      opsPerSecond: (successes / duration) * 1000,
      averageLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
      peakLatency: Math.max(...latencies),
      failures,
      memoryUsage: endMemory - startMemory,
    };

    this.results.push(result);
    return result;
  }

  /**
   * Benchmark Lock-Free Ingestion Channel
   */
  async benchmarkIngestionChannel(config: BenchmarkConfig): Promise<BenchmarkResult> {
    const channel = new LockFreeIngestionChannel("benchmark", {
      capacity: config.capacity,
      enableMetrics: true,
      enableBatching: true,
      batchSize: 32,
    });

    const startTime = Date.now();
    const startMemory = process.memoryUsage().heapUsed;

    let successes = 0;
    let failures = 0;
    const latencies: number[] = [];

    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i, value: Math.random() },
        timestamp: Date.now(),
      };

      const enqueueStart = Date.now();
      const enqueued = channel.publish(packet);
      const enqueueLatency = Date.now() - enqueueStart;

      if (enqueued) {
        successes++;
        latencies.push(enqueueLatency);
        
        // Consume in batches
        if (i % 32 === 0) {
          channel.subscribe(32);
        }
      } else {
        failures++;
      }
    }

    // Drain remaining items
    while (!channel.isEmpty()) {
      channel.subscribe(32);
    }

    const duration = Date.now() - startTime;
    const endMemory = process.memoryUsage().heapUsed;

    const result: BenchmarkResult = {
      name: "Lock-Free Ingestion Channel",
      operations: successes,
      durationMs: duration,
      opsPerSecond: (successes / duration) * 1000,
      averageLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
      peakLatency: Math.max(...latencies),
      failures,
      memoryUsage: endMemory - startMemory,
    };

    this.results.push(result);
    return result;
  }

  /**
   * Run all benchmarks
   */
  async runAllBenchmarks(): Promise<void> {
    console.log("=== Lock-Free Queue Performance Benchmarks ===\n");

    const configs: BenchmarkConfig[] = [
      { iterations: 10000, capacity: 1024, producerThreads: 1, consumerThreads: 1, dataSize: 100 },
      { iterations: 100000, capacity: 1024, producerThreads: 1, consumerThreads: 1, dataSize: 100 },
      { iterations: 1000000, capacity: 4096, producerThreads: 1, consumerThreads: 1, dataSize: 100 },
    ];

    for (const config of configs) {
      console.log(`\n--- Benchmark Configuration ---`);
      console.log(`Iterations: ${config.iterations}`);
      console.log(`Capacity: ${config.capacity}`);
      console.log(`Producer Threads: ${config.producerThreads}`);
      console.log(`Consumer Threads: ${config.consumerThreads}`);
      console.log(`Data Size: ${config.dataSize} bytes\n`);

      // Run benchmarks
      await this.benchmarkSPSCLockFree(config);
      await this.benchmarkMPSCLockFree(config);
      await this.benchmarkTraditionalQueue(config);
      await this.benchmarkBackpressureManager(config);
      await this.benchmarkIngestionChannel(config);

      this.printResults();
      this.results = []; // Clear for next config
    }
  }

  /**
   * Print benchmark results
   */
  private printResults(): void {
    console.log("\n--- Benchmark Results ---");
    console.log(
      `${"Name".padEnd(35)} | ${"Ops/sec".padEnd(12)} | ${"Avg Latency".padEnd(12)} | ${"Peak Latency".padEnd(12)} | ${"Failures".padEnd(10)} | ${"Memory (MB)".padEnd(12)}`
    );
    console.log("-".repeat(120));

    for (const result of this.results) {
      console.log(
        `${result.name.padEnd(35)} | ${result.opsPerSecond.toFixed(0).padStart(12)} | ${result.averageLatency.toFixed(3).padStart(12)} | ${result.peakLatency.toFixed(3).padStart(12)} | ${result.failures.toString().padStart(10)} | ${(result.memoryUsage / 1024 / 1024).toFixed(2).padStart(12)}`
      );
    }

    // Calculate improvement
    const lockFree = this.results.find(r => r.name.includes("SPSC"));
    const traditional = this.results.find(r => r.name.includes("Traditional"));

    if (lockFree && traditional) {
      const improvement = ((lockFree.opsPerSecond - traditional.opsPerSecond) / traditional.opsPerSecond) * 100;
      console.log(`\nPerformance Improvement: ${improvement.toFixed(1)}%`);
    }
  }

  /**
   * Run stress test
   */
  async runStressTest(): Promise<void> {
    console.log("\n=== Stress Test ===\n");

    const config: BenchmarkConfig = {
      iterations: 10000000,
      capacity: 8192,
      producerThreads: 1,
      consumerThreads: 1,
      dataSize: 100,
    };

    const buffer = new LockFreeRingBuffer<IngestionPacket>({
      capacity: config.capacity,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });

    const startTime = Date.now();
    let successes = 0;
    let failures = 0;

    for (let i = 0; i < config.iterations; i++) {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { index: i },
        timestamp: Date.now(),
      };

      if (buffer.tryEnqueue(packet)) {
        successes++;
        buffer.tryDequeue();
      } else {
        failures++;
      }

      // Progress indicator
      if (i % 1000000 === 0) {
        console.log(`Progress: ${((i / config.iterations) * 100).toFixed(1)}%`);
      }
    }

    const duration = Date.now() - startTime;
    const metrics = buffer.getMetrics();

    console.log(`\nStress Test Results:`);
    console.log(`Total Operations: ${config.iterations}`);
    console.log(`Successful: ${successes}`);
    console.log(`Failed: ${failures}`);
    console.log(`Duration: ${duration}ms`);
    console.log(`Ops/sec: ${(successes / duration) * 1000}`);
    console.log(`Final Utilization: ${(metrics.utilization * 100).toFixed(2)}%`);
    console.log(`Total Enqueued: ${metrics.totalEnqueued}`);
    console.log(`Total Dequeued: ${metrics.totalDequeued}`);
  }
}

// Run benchmarks if executed directly
async function main() {
  const suite = new BenchmarkSuite();

  try {
    await suite.runAllBenchmarks();
    await suite.runStressTest();
  } catch (error) {
    console.error("Benchmark failed:", error);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

export { BenchmarkSuite, BenchmarkResult, BenchmarkConfig };
