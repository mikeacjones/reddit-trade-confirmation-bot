package activities

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"go.temporal.io/api/workflowservice/v1"
	"go.temporal.io/sdk/client"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/deployment"
)

const (
	deploymentLabel     = "com.reddit-bots.deployment-name"
	buildIDLabel        = "com.reddit-bots.build-id"
	imageLabel          = "com.reddit-bots.image"
	defaultDockerSocket = "/var/run/docker.sock"
)

var deploymentActivityTypes = map[string]struct{}{
	"collect_sdk_metrics":                        {},
	"count_completed_workflows_for_deployment":   {},
	"count_failed_workflows_for_deployment":      {},
	"describe_worker_deployment":                 {},
	"list_deployment_containers":                 {},
	"remove_deployment_container":                {},
	"rollback_worker_deployment":                 {},
}

var prometheusSampleRE = regexp.MustCompile(
	`^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+0-9.eE]+)`,
)

type unixDialer struct{ path string }

func (d unixDialer) Dial(_, _ string) (net.Conn, error) {
	return net.Dial("unix", d.path)
}

func dockerSocketPath() string {
	if p := os.Getenv("DOCKER_SOCKET"); p != "" {
		return p
	}
	return defaultDockerSocket
}

func dockerRequest(method, path string, body any, okStatuses map[int]struct{}) (json.RawMessage, error) {
	socketPath := dockerSocketPath()
	if _, err := os.Stat(socketPath); err != nil {
		return nil, fmt.Errorf("Docker socket not found: %s", socketPath)
	}
	if okStatuses == nil {
		okStatuses = map[int]struct{}{200: {}, 201: {}, 204: {}}
	}
	var bodyReader io.Reader
	headers := http.Header{}
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		bodyReader = strings.NewReader(string(b))
		headers.Set("Content-Type", "application/json")
	}
	transport := &http.Transport{Dial: unixDialer{path: socketPath}.Dial}
	httpClient := &http.Client{Transport: transport, Timeout: 30 * time.Second}
	req, err := http.NewRequest(method, "http://localhost"+path, bodyReader)
	if err != nil {
		return nil, err
	}
	req.Header = headers
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if _, ok := okStatuses[resp.StatusCode]; !ok {
		return nil, fmt.Errorf("Docker API %s %s failed with %d: %s", method, path, resp.StatusCode, raw)
	}
	if len(raw) == 0 {
		return nil, nil
	}
	return json.RawMessage(raw), nil
}

func visibilityEscape(value string) string {
	return strings.ReplaceAll(strings.ReplaceAll(value, `\`, `\\`), `"`, `\"`)
}

// DescribeWorkerDeployment describes a Temporal Worker Deployment and its versions.
func (a *Activities) DescribeWorkerDeployment(ctx context.Context, deploymentName string) (deployment.WorkerDeploymentState, error) {
	handle := a.Temporal.WorkerDeploymentClient().GetHandle(deploymentName)
	resp, err := handle.Describe(ctx, client.WorkerDeploymentDescribeOptions{})
	if err != nil {
		return deployment.WorkerDeploymentState{}, err
	}
	currentBuild := ""
	if resp.Info.RoutingConfig.CurrentVersion != nil {
		currentBuild = resp.Info.RoutingConfig.CurrentVersion.BuildID
	}
	rampingBuild := ""
	if resp.Info.RoutingConfig.RampingVersion != nil {
		rampingBuild = resp.Info.RoutingConfig.RampingVersion.BuildID
	}
	versions := make([]deployment.WorkerVersionState, 0, len(resp.Info.VersionSummaries))
	for _, summary := range resp.Info.VersionSummaries {
		buildID := summary.Version.BuildID
		var drainage *string
		if summary.DrainageStatus != client.WorkerDeploymentVersionDrainageStatusUnspecified {
			s := drainageStatusString(summary.DrainageStatus)
			drainage = &s
		}
		versions = append(versions, deployment.WorkerVersionState{
			BuildID:        buildID,
			Status:         deriveVersionStatus(buildID, currentBuild, rampingBuild, summary.DrainageStatus),
			DrainageStatus: drainage,
		})
	}
	return deployment.WorkerDeploymentState{
		DeploymentName: deploymentName,
		Versions:       versions,
	}, nil
}

func deriveVersionStatus(buildID, currentBuild, rampingBuild string, drainage client.WorkerDeploymentVersionDrainageStatus) string {
	if buildID != "" && buildID == currentBuild {
		return "CURRENT"
	}
	if buildID != "" && buildID == rampingBuild {
		return "RAMPING"
	}
	switch drainage {
	case client.WorkerDeploymentVersionDrainageStatusDraining:
		return "DRAINING"
	case client.WorkerDeploymentVersionDrainageStatusDrained:
		return "DRAINED"
	default:
		return "INACTIVE"
	}
}

func drainageStatusString(s client.WorkerDeploymentVersionDrainageStatus) string {
	switch s {
	case client.WorkerDeploymentVersionDrainageStatusDraining:
		return "DRAINING"
	case client.WorkerDeploymentVersionDrainageStatusDrained:
		return "DRAINED"
	default:
		return "UNSPECIFIED"
	}
}

func (a *Activities) countWorkflowsForDeployment(
	ctx context.Context,
	deploymentName, buildID, sinceTimeISO, status string,
) (deployment.TemporalExecutionSummary, error) {
	deploymentVersion := deploymentName + ":" + buildID
	query := fmt.Sprintf(
		`ExecutionStatus = "%s" AND CloseTime >= "%s" AND TemporalWorkerDeployment = "%s" AND TemporalWorkerDeploymentVersion = "%s"`,
		status, sinceTimeISO, visibilityEscape(deploymentName), visibilityEscape(deploymentVersion),
	)
	var workflowIDs []string
	var nextPageToken []byte
	for {
		resp, err := a.Temporal.WorkflowService().ListWorkflowExecutions(ctx, &workflowservice.ListWorkflowExecutionsRequest{
			Namespace:     a.Cfg.TemporalNamespace,
			PageSize:      100,
			NextPageToken: nextPageToken,
			Query:         query,
		})
		if err != nil {
			return deployment.TemporalExecutionSummary{}, err
		}
		for _, exec := range resp.Executions {
			workflowIDs = append(workflowIDs, exec.Execution.WorkflowId)
		}
		nextPageToken = resp.NextPageToken
		if len(nextPageToken) == 0 {
			break
		}
	}
	ids := workflowIDs
	if len(ids) > 20 {
		ids = ids[:20]
	}
	return deployment.TemporalExecutionSummary{Count: len(workflowIDs), WorkflowIDs: ids}, nil
}

// CountFailedWorkflowsForDeployment counts recently failed workflows for a build.
func (a *Activities) CountFailedWorkflowsForDeployment(ctx context.Context, deploymentName, buildID, sinceTimeISO string) (deployment.TemporalExecutionSummary, error) {
	return a.countWorkflowsForDeployment(ctx, deploymentName, buildID, sinceTimeISO, "Failed")
}

// CountCompletedWorkflowsForDeployment counts recently completed workflows for a build.
func (a *Activities) CountCompletedWorkflowsForDeployment(ctx context.Context, deploymentName, buildID, sinceTimeISO string) (deployment.TemporalExecutionSummary, error) {
	return a.countWorkflowsForDeployment(ctx, deploymentName, buildID, sinceTimeISO, "Completed")
}

// CollectSDKMetrics collects Temporal SDK failure counters from the metrics endpoint.
func (a *Activities) CollectSDKMetrics(ctx context.Context, healthCheck deployment.HealthCheck, subredditName string) (deployment.SDKMetricsSnapshot, error) {
	metricsURL := ""
	if healthCheck.MetricsURL != nil {
		metricsURL = *healthCheck.MetricsURL
	}
	if metricsURL == "" {
		metricsURL = os.Getenv("DEPLOYMENT_HEALTH_METRICS_URL")
	}
	if metricsURL == "" {
		bind := strings.TrimSpace(os.Getenv("TEMPORAL_SDK_METRICS_BIND_ADDRESS"))
		if bind != "" {
			parts := strings.Split(bind, ":")
			port := parts[len(parts)-1]
			metricsURL = "http://127.0.0.1:" + port + "/metrics"
		}
	}
	if metricsURL == "" {
		return deployment.SDKMetricsSnapshot{}, fmt.Errorf("No SDK metrics URL configured")
	}
	httpClient := &http.Client{Timeout: 5 * time.Second}
	resp, err := httpClient.Get(metricsURL)
	if err != nil {
		return deployment.SDKMetricsSnapshot{}, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return deployment.SDKMetricsSnapshot{}, err
	}
	text := string(raw)
	common := metricFilter{
		namespace:     a.Cfg.TemporalNamespace,
		taskQueue:     a.Cfg.TaskQueue,
		subredditName: subredditName,
	}
	return deployment.SDKMetricsSnapshot{
		WorkflowCompleted: sumPrometheus(text, []string{"temporal_workflow_completed"}, common, nil),
		ActivityCompleted: sumPrometheus(text, []string{
			"temporal_activity_execution_completed",
			"temporal_activity_execution_latency_count",
		}, common, deploymentActivityTypes),
		WorkflowFailed: sumPrometheus(text, []string{"temporal_workflow_failed"}, common, nil),
		ActivityFailed: sumPrometheus(text, []string{"temporal_activity_execution_failed"}, common, nil),
		WorkflowTaskFailed: sumPrometheus(text, []string{
			"temporal_workflow_task_execution_failed",
			"temporal_workflow_task_failed",
		}, common, nil),
	}, nil
}

type metricFilter struct {
	namespace, taskQueue, subredditName string
}

func sumPrometheus(text string, names []string, f metricFilter, exclude map[string]struct{}) float64 {
	nameSet := map[string]struct{}{}
	for _, n := range names {
		nameSet[n] = struct{}{}
	}
	var total float64
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		m := prometheusSampleRE.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		name := m[1]
		base := strings.TrimSuffix(name, "_total")
		if _, ok := nameSet[name]; !ok {
			if _, ok2 := nameSet[base]; !ok2 {
				continue
			}
		}
		labels := parsePromLabels(m[2])
		if exclude != nil {
			if at, ok := labels["activity_type"]; ok {
				if _, skip := exclude[at]; skip {
					continue
				}
			}
		}
		if v, ok := labels["namespace"]; ok && v != f.namespace {
			continue
		}
		if v, ok := labels["task_queue"]; ok && v != f.taskQueue {
			continue
		}
		if v, ok := labels["subreddit"]; ok && v != f.subredditName {
			continue
		}
		var val float64
		fmt.Sscanf(m[3], "%f", &val)
		total += val
	}
	return total
}

func parsePromLabels(raw string) map[string]string {
	out := map[string]string{}
	if raw == "" {
		return out
	}
	re := regexp.MustCompile(`([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"`)
	for _, m := range re.FindAllStringSubmatch(raw, -1) {
		out[m[1]] = strings.ReplaceAll(m[2], `\"`, `"`)
	}
	return out
}

// ListDeploymentContainers lists Docker containers for a deployment.
func (a *Activities) ListDeploymentContainers(ctx context.Context, deploymentName string) ([]deployment.DockerContainerState, error) {
	filters, _ := json.Marshal(map[string][]string{"label": {deploymentLabel + "=" + deploymentName}})
	q := url.Values{"all": {"true"}, "filters": {string(filters)}}.Encode()
	raw, err := dockerRequest("GET", "/containers/json?"+q, nil, nil)
	if err != nil {
		return nil, err
	}
	var containers []struct {
		ID     string            `json:"Id"`
		Names  []string          `json:"Names"`
		Image  string            `json:"Image"`
		State  string            `json:"State"`
		Status string            `json:"Status"`
		Labels map[string]string `json:"Labels"`
	}
	if err := json.Unmarshal(raw, &containers); err != nil {
		return nil, err
	}
	var result []deployment.DockerContainerState
	for _, c := range containers {
		buildID := c.Labels[buildIDLabel]
		if buildID == "" {
			continue
		}
		name := c.ID[:12]
		if len(c.Names) > 0 {
			name = strings.TrimPrefix(c.Names[0], "/")
		}
		img := c.Labels[imageLabel]
		if img == "" {
			img = c.Image
		}
		state, status := c.State, c.Status
		result = append(result, deployment.DockerContainerState{
			ID:      c.ID,
			Name:    name,
			BuildID: buildID,
			Image:   &img,
			State:   &state,
			Status:  &status,
		})
	}
	return result, nil
}

// RollbackWorkerDeployment sets current version back and removes the failed container.
func (a *Activities) RollbackWorkerDeployment(ctx context.Context, deploymentName, rollbackBuildID string, failedContainer *deployment.DockerContainerState) (deployment.RollbackResult, error) {
	handle := a.Temporal.WorkerDeploymentClient().GetHandle(deploymentName)
	_, err := handle.SetCurrentVersion(ctx, client.WorkerDeploymentSetCurrentVersionOptions{
		BuildID:  rollbackBuildID,
		Identity: "reddit-bots-deployment-health",
	})
	if err != nil {
		return deployment.RollbackResult{}, err
	}
	result := deployment.RollbackResult{
		DeploymentName:  deploymentName,
		RollbackBuildID: rollbackBuildID,
	}
	if failedContainer != nil {
		ref := url.PathEscape(failedContainer.ID)
		_, err := dockerRequest("DELETE", "/containers/"+ref+"?force=true&v=true", nil, map[int]struct{}{204: {}, 404: {}})
		if err != nil {
			return result, err
		}
		result.ContainerRemoved = true
	}
	return result, nil
}

// RemoveDeploymentContainer removes a drained deployment container and unused image.
func (a *Activities) RemoveDeploymentContainer(ctx context.Context, container deployment.DockerContainerState) (deployment.DockerCleanupResult, error) {
	result := deployment.DockerCleanupResult{ContainerName: container.Name}
	ref := url.PathEscape(container.ID)
	if _, err := dockerRequest("DELETE", "/containers/"+ref+"?force=true&v=true", nil, map[int]struct{}{204: {}, 404: {}}); err != nil {
		return result, err
	}
	result.ContainerRemoved = true

	filters, _ := json.Marshal(map[string][]string{"label": {buildIDLabel + "=" + container.BuildID}})
	q := url.Values{"filters": {string(filters)}}.Encode()
	raw, err := dockerRequest("POST", "/containers/prune?"+q, nil, nil)
	if err == nil && raw != nil {
		var prune struct {
			ContainersDeleted []string `json:"ContainersDeleted"`
		}
		_ = json.Unmarshal(raw, &prune)
		result.PrunedContainers = len(prune.ContainersDeleted)
	}

	if container.Image != nil && *container.Image != "" {
		imgRef := url.PathEscape(*container.Image)
		if _, err := dockerRequest("DELETE", "/images/"+imgRef+"?force=false&noprune=false", nil, map[int]struct{}{200: {}, 202: {}, 204: {}, 404: {}}); err != nil {
			msg := err.Error()
			result.ImageRemoveError = &msg
		} else {
			result.ImageRemoved = true
		}
	}
	return result, nil
}
