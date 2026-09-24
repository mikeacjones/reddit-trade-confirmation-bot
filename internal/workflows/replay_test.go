package workflows_test

import (
	"os"
	"path/filepath"
	"testing"

	"go.temporal.io/sdk/worker"
	"go.temporal.io/sdk/workflow"

	wf "github.com/mikeacjones/reddit-trade-confirmation-bot/internal/workflows"
)

func TestReplayDeterminism(t *testing.T) {
	dir := os.Getenv("REPLAY_HISTORIES_DIR")
	if dir == "" {
		dir = "replay_histories"
	}
	if _, err := os.Stat(dir); err != nil {
		t.Skipf("histories directory not found: %s", dir)
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) == 0 {
		t.Fatalf("no JSON history files in %s", dir)
	}

	replayer := worker.NewWorkflowReplayer()
	replayer.RegisterWorkflowWithOptions(wf.CommentPollingWorkflow, workflow.RegisterOptions{Name: "CommentPollingWorkflow"})
	replayer.RegisterWorkflowWithOptions(wf.ProcessConfirmationWorkflow, workflow.RegisterOptions{Name: "ProcessConfirmationWorkflow"})
	replayer.RegisterWorkflowWithOptions(wf.FlairCoordinatorWorkflow, workflow.RegisterOptions{Name: "FlairCoordinatorWorkflow"})
	replayer.RegisterWorkflowWithOptions(wf.MonthlyPostWorkflow, workflow.RegisterOptions{Name: "MonthlyPostWorkflow"})
	replayer.RegisterWorkflowWithOptions(wf.DeploymentCleanupWorkflow, workflow.RegisterOptions{Name: "DeploymentCleanupWorkflow"})

	for _, f := range files {
		f := f
		t.Run(filepath.Base(f), func(t *testing.T) {
			if err := replayer.ReplayWorkflowHistoryFromJSONFile(nil, f); err != nil {
				t.Fatal(err)
			}
		})
	}
}
